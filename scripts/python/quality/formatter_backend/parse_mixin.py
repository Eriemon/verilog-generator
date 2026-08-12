"""为 VerilogFormatterEngine 提供模块、参数和端口声明解析辅助。"""

# future annotations 让 mixin 拆分后的类型提示继续延迟求值。
from __future__ import annotations

# 正则、时间兼容字段和路径兼容字段都服务于旧版解析入口。
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable

# banner 工具负责识别和复用现有的模块头分组注释样式。
from .banners import display_width, extract_banner_title, is_banner_line, make_banner

# formatter 声明模型第一组承载参数与端口声明的核心结构。
from .models import (
    VerilogFormatterError,
    ParamDecl,
    ParamRenderCluster,
)

# formatter 声明模型第二组承载端口布局和信号声明的辅助结构。
from .models import (
    PortDecl,
    PortLayoutInfo,
    SignalDecl,
)

# formatter 声明模型第三组承载模块头元数据。
from .models import (
    HeaderMetadata,
)

# formatter 布局模型承载输出、赋值和实例信号的布局辅助结构。
from .models import (
    OutputSignalLayout,
    AssignSourceLayout,
    InstanceSignalLayout,
    AssignStmt,
    LValueRef,
)

# formatter 语句块模型第一组承载主体和 case 结构。
from .models import (
    BodyBlock,
    CaseItem,
    ControlNode,
)

# formatter 语句块模型第二组承载过程块和实例块结构。
from .models import (
    AlwaysBlock,
    InstanceBlock,
    GenerateBlock,
)

# formatter 语句块模型第三组承载其余原样保留或条件编译结构。
from .models import (
    InitialBlock,
    FunctionBlock,
    RawBlock,
    PreprocessorConditional,
)

# 文本读取工具保留给旧继承入口，避免拆分 mixin 后改变可导入符号集合。
from .textio import read_verilog_text

# 维护模块头、参数和端口等声明区域的解析辅助逻辑。
class ParseMixin:
    """维护模块、参数、端口和声明文本的结构化解析逻辑。"""

    # 按 module/endmodule 边界切分多模块源码片段。
    def _split_module_sections(self, source: str) -> list[dict[str, object]]:
        """
        把源码切分成若干个完整模块片段及其前置注释。

        参数:
            source: 原始 Verilog 源码全文。
        返回:
            list[dict[str, object]]: 每个元素包含模块原文和紧邻其前的行注释列表。
        异常:
            VerilogFormatterError: 源码中找不到任何 module 声明时抛出。
        """

        # 编译跨多行 module/endmodule 片段匹配模式。
        pattern_module: re.Pattern[str] = re.compile(r"(?ms)^\s*module\b.*?^\s*endmodule\b")  # 完整模块片段匹配模式

        # 收集源码中命中的全部完整模块片段。
        list_matches = list(pattern_module.finditer(source))  # 完整模块匹配结果列表

        # 没有 module 声明时，builtin backend 无法继续解析。
        if not list_matches:

            # 抛出带 current-project 前缀的模块缺失异常。
            raise VerilogFormatterError(
                "> ERR: [Python] source does not contain a module declaration."
            ) from self._strict_error(
                "unsupported_shape",
                "source does not contain a module declaration",
                "Provide at least one synthesizable module declaration before running the formatter.",
            )

        # 逐个模块片段写入模块原文和前置注释信息。
        list_sections: list[dict[str, object]] = []  # 模块片段及其前置注释列表

        # 记录上一个模块结束位置，用来提取当前模块前的注释区。
        int_last_end = 0  # 上一个模块在源码中的结束下标

        # 顺序遍历每个 module/endmodule 匹配结果。
        for match_module in list_matches:

            # 收集当前模块紧邻前方的连续行注释。
            list_leading_comments: list[str] = []  # 当前模块前置注释列表

            # 截出上一个模块结束后到当前模块开始前的夹层文本。
            str_between = source[int_last_end : match_module.start()]  # 当前模块前的夹层文本

            # 逆序扫描夹层文本，只保留紧邻模块的连续注释段。
            for str_line in reversed(str_between.splitlines()):

                # 对当前物理行去掉两端空白，便于判断注释边界。
                str_stripped_line = str_line.strip()  # 当前夹层物理行的规范化文本

                # 空白行只在尚未开始收集注释时允许被跳过。
                if not str_stripped_line:

                    # 已经命中过前置注释后，空白行意味着注释段结束。
                    if list_leading_comments:

                        # 前置注释段一旦被空白行打断，就不再继续向前扩展。
                        break

                    # 夹层前部仍是纯空白时，继续逆序跳过这些行。
                    continue

                # 双斜线注释属于模块前置注释候选。
                if str_stripped_line.startswith("//"):

                    # 记录这条紧邻模块的行注释。
                    list_leading_comments.append(str_stripped_line)

                    # 当前行已经并入前置注释段，继续向前尝试补齐这一段注释。
                    continue

                # 命中非注释内容时，前置注释段到此结束。
                break

            # 追加当前模块原文和正序前置注释列表。
            list_sections.append(
                {
                    "module_text": match_module.group(0),
                    "leading_comments": list(reversed(list_leading_comments)),
                }
            )

            # 更新下一个模块的夹层起点。
            int_last_end = match_module.end()  # 当前模块在源码中的结束下标

        # 返回全部模块片段及其前置注释信息。
        return list_sections

    # 解析单模块源码的名称、参数、端口和主体正文。
    def _parse_module(self, source: str) -> tuple[str, list[ParamDecl], list[PortDecl], str]:
        """
        从单模块源码中提取模块名、参数、端口和主体正文。

        参数:
            source: 只包含一个模块定义的源码文本。
        返回:
            tuple[str, list[ParamDecl], list[PortDecl], str]: 模块名、参数列表、端口列表和模块主体正文。
        异常:
            VerilogFormatterError: 源码不是单模块形态或模块头不受支持时抛出。
        """

        # builtin backend 目前只接受单个 module 定义。
        if len(re.findall(r"(?m)^\s*module\b", source)) != 1:

            # 抛出多模块源码不受支持的统一异常。
            raise VerilogFormatterError(
                "> ERR: [Python] multi-module sources are not supported by the builtin backend."
            ) from self._strict_error(
                "unsupported_shape",
                "multi-module sources are not supported by the builtin backend",
                "Split helper modules into separate files before formatting.",
            )

        # 先拼出单模块头、参数段、端口段和主体段的整体正则文本。
        str_module_header_pattern = (
            r"module\s+(?P<name>\w+)\s*"
            r"(?:#\s*\((?P<params>.*?)\)\s*(?://[^\n]*\n\s*)*)?"
            r"\((?P<ports>.*?)\)\s*;\s*(?P<body>.*)\s*endmodule"
        )  # 单模块解析的整体正则文本

        # 基于整体正则文本编译单模块结构匹配模式。
        pattern_module_header: re.Pattern[str] = re.compile(str_module_header_pattern, re.DOTALL)  # 单模块解析的整体匹配模式

        # 在源码里查找单模块结构的完整匹配结果。
        match_module = pattern_module_header.search(source)  # 单模块整体匹配结果

        # 匹配失败时，说明模块头结构超出 builtin backend 处理边界。
        if not match_module:

            # 抛出单模块头不受支持的统一异常。
            raise VerilogFormatterError(
                "> ERR: [Python] only single module sources are currently supported."
            ) from self._strict_error(
                "unsupported_shape",
                "only single module sources are currently supported",
                "Simplify the module header so the builtin backend can parse one module body at a time.",
            )

        # 提取模块名，作为后续渲染和日志输出的主标识。
        str_module_name = match_module.group("name")  # 当前模块名称

        # 预先加载源码里的宏展开映射，供参数和端口解析复用。
        dict_macro_expansions = self._load_macro_expansions_from_source(source)  # 当前源码里的宏展开映射

        # 解析模块头里的参数声明列表。
        list_params = self._parse_params(match_module.group("params") or "", dict_macro_expansions)  # 模块参数列表

        # 提取模块主体正文，交给后续声明和语句块解析继续使用。
        str_body = match_module.group("body")  # 模块主体正文

        # 解析端口声明，并在需要时同步清理主体里的冗余端口文本。
        str_ports_text = match_module.group("ports") or ""  # 模块头端口原始文本

        # 先收集模块头端口恢复结果，便于后面统一拆包。
        tuple_module_ports = self._parse_module_ports(str_ports_text, str_body, dict_macro_expansions)  # 模块头端口恢复结果

        # 把端口恢复结果拆成端口列表和清理后的主体正文。
        list_ports, str_body = tuple_module_ports  # 模块端口列表与清理后的主体正文

        # 返回单模块解析得到的核心结构。
        return str_module_name, list_params, list_ports, str_body

    # 解析模块头参数段里的参数声明列表。
    def _parse_params(self, text: str, macro_expansions: dict[str, str] | None = None) -> list[ParamDecl]:
        """
        解析参数块文本中的参数声明列表。

        参数:
            text: 参数块原始文本。
            macro_expansions: 供参数宏引用展开使用的宏映射。
        返回:
            list[ParamDecl]: 解析得到的参数声明列表。
        """

        # 纯空参数块可以直接返回空列表。
        if not text.strip():

            # 没有任何参数文本时，参数列表应保持为空。
            return []

        # 缺省时使用空宏映射，避免后续分支反复判空。
        dict_macro_expansions = macro_expansions or {}  # 参数解析使用的宏展开映射

        # 逐行 helper 优先保留注释和宏调用的局部结构。
        list_params = self._parse_param_lines(text, dict_macro_expansions)  # 逐行模式解析出的参数声明

        # 逐行模式已经命中过参数时，直接返回逐行解析结果。
        if list_params:

            # 逐行路径已经拿到稳定结果，不再回退到顶层逗号切分。
            return list_params

        # 逐行模式未命中时，回退到顶层逗号切分方案。
        return self._parse_param_entries(self._split_top_level(text, ","))

    # 参数行 helper 负责逐行路由普通声明和宏调用。
    def _parse_param_lines(self, text: str, dict_macro_expansions: dict[str, str]) -> list[ParamDecl]:
        """按物理行解析参数声明并保留宏调用原文。

        参数:
            text: 参数块原始文本。
            dict_macro_expansions: 参数宏引用对应的展开文本。
        返回:
            逐行模式解析得到的参数声明列表。
        """

        # 逐行结果保持源码中的参数和宏调用顺序。
        list_params: list[ParamDecl] = []  # 逐行参数声明列表

        # 每个物理行独立路由到宏或普通参数解析器。
        for str_raw_line in text.splitlines():

            # 去掉外围空白，便于识别空行、注释和宏调用。
            str_stripped_line = str_raw_line.strip()  # 当前参数行的规范化文本

            # 空行和纯注释行不产生参数声明。
            if not str_stripped_line or str_stripped_line.startswith("//"):

                # 当前物理行不承载参数声明。
                continue

            # 宏调用需要同时保留 raw 节点和可用的合成展开参数。
            if str_stripped_line.startswith("`"):

                # 宏 helper 返回按 raw、synthetic 顺序排列的参数节点。
                list_params.extend(self._parse_param_macro_line(str_stripped_line, dict_macro_expansions))

                # 当前宏行已经完整消费。
                continue

            # 普通参数行通过单条声明解析器恢复结构。
            parsed_param_decl = self._parse_param_line(str_stripped_line, allow_trailing_comma=True)  # 当前物理行解析结果

            # 只收集可稳定识别的参数声明。
            if parsed_param_decl is not None:

                # 保留逐行参数的源码顺序。
                list_params.append(parsed_param_decl)

        # 返回逐行模式命中的全部参数节点。
        return list_params

    # 参数宏 helper 保留调用原文并把展开项重建为 synthetic 节点。
    def _parse_param_macro_line(
        self,
        str_macro_line: str,
        dict_macro_expansions: dict[str, str],
    ) -> list[ParamDecl]:
        """解析一行参数宏调用及其可用展开文本。

        参数:
            str_macro_line: 以反引号开头的参数宏调用行。
            dict_macro_expansions: 宏名到展开文本的映射。
        返回:
            raw 宏节点以及随后可生成的 synthetic 参数节点。
        """

        # raw 节点始终保留用户显式写下的宏调用文本。
        list_params = [ParamDecl("raw", "", "", raw_text=str_macro_line)]  # 当前宏行产生的参数节点

        # 宏名用于从源码级映射中查找展开文本。
        str_macro_name = str_macro_line.lstrip("`").split(None, 1)[0]  # 当前参数行引用的宏名

        # 缺少展开文本时只返回 raw 节点。
        str_expansion = dict_macro_expansions.get(str_macro_name, "")  # 当前宏调用对应的展开文本

        # 空展开不产生 synthetic 参数。
        if not str_expansion:

            # 调用方仍可依赖 raw 节点保留原文。
            return list_params

        # 展开文本递归复用参数解析入口，但禁用二次宏展开。
        list_expanded_params = self._parse_params(str_expansion, {})  # 宏展开后得到的候选参数列表

        # 顺序处理每个展开候选，过滤 raw 宏占位节点。
        for parsed_param in list_expanded_params:

            # raw 节点只负责保留展开文本中的宏调用，不重复合成。
            if parsed_param.raw_text:

                # 跳过无法落成结构声明的宏占位节点。
                continue

            # 重建节点时复制前置注释，避免共享可变列表。
            list_comments = list(parsed_param.leading_comments)  # synthetic 参数使用的注释副本

            # synthetic 节点继承展开参数的声明规格和注释。
            list_params.append(
                ParamDecl(
                    parsed_param.keyword,  # 展开参数关键字
                    parsed_param.name,  # 展开参数名称
                    parsed_param.value,  # 展开参数默认值

                    # 声明规格和注释继续继承原展开参数，但前置注释使用隔离副本。
                    parsed_param.decl_spec,  # 展开参数声明规格
                    parsed_param.comment,  # 展开参数行尾注释
                    list_comments,  # 隔离后的前置注释副本

                    # synthetic 标记让 renderer 区分宏原文与可落地声明。
                    synthetic=True,  # 标记节点来自宏展开
                )
            )

        # 返回 raw 节点和全部可落地的 synthetic 参数。
        return list_params

    # 单参数行 helper 统一拆分行尾注释和调用声明解析器。
    def _parse_param_line(self, str_entry: str, *, allow_trailing_comma: bool) -> ParamDecl | None:
        """解析一条普通参数声明文本。

        参数:
            str_entry: 单条参数声明或顶层逗号片段。
            allow_trailing_comma: 是否允许声明末尾保留逗号。
        返回:
            可稳定解析的参数声明；无法识别时返回 None。
        """

        # 行尾注释与声明主体分别交给既有单条解析器。
        str_raw_decl, str_comment = self._split_comment(str_entry)  # 参数声明文本与行尾注释

        # 统一禁用参数分号，逗号策略由调用路径决定。
        return self._parse_param_decl(
            str_raw_decl,
            str_comment,
            allow_trailing_comma=allow_trailing_comma,
            allow_trailing_semicolon=False,
        )

    # 顶层参数片段 helper 承担逐行模式未命中时的保守回退。
    def _parse_param_entries(self, list_entries: list[str]) -> list[ParamDecl]:
        """解析顶层逗号切分得到的参数片段。

        参数:
            list_entries: 保留原始顺序的顶层参数片段。
        返回:
            回退路径识别出的结构化参数声明列表。
        """

        # 回退结果继续保持顶层片段顺序。
        list_params: list[ParamDecl] = []  # 顶层片段解析结果

        # 每个片段独立执行单参数解析。
        for str_entry in list_entries:

            # 回退片段已经由顶层逗号 splitter 去除分隔符。
            parsed_param_decl = self._parse_param_line(str_entry, allow_trailing_comma=False)  # 当前回退片段解析结果

            # 无法识别的片段保持既有忽略行为。
            if parsed_param_decl is None:

                # 继续处理其它顶层参数片段。
                continue

            # 结构化声明按原始片段顺序加入结果。
            list_params.append(parsed_param_decl)

        # 返回回退路径解析出的全部参数。
        return list_params

    # 解析单条 parameter/localparam 声明。
    def _parse_param_decl(
        self,
        text: str,
        comment: str = "",
        *,
        allow_trailing_comma: bool,
        allow_trailing_semicolon: bool,
    ) -> ParamDecl | None:
        """
        解析单条 parameter 或 localparam 声明。

        参数:
            text: 单条参数声明文本。
            comment: 参数声明关联的行尾注释。
            allow_trailing_comma: 是否允许声明尾部保留逗号。
            allow_trailing_semicolon: 是否允许声明尾部保留分号。
        返回:
            ParamDecl | None: 解析成功时返回参数声明，否则返回 None。
        """

        # 先裁掉两端空白，得到待解析的参数声明主体。
        str_cleaned = text.strip()  # 待解析的参数声明主体

        # 空参数文本不产生参数声明。
        if not str_cleaned:

            # 只含空白的候选文本不生成结构化参数声明。
            return None

        # 逗号属于声明分隔符，需要先剥离后再判断参数主体。
        if allow_trailing_comma and str_cleaned.endswith(","):

            # 去掉尾逗号后，后续正则才会只面对真实参数主体。
            str_cleaned = str_cleaned[:-1].rstrip()  # 去掉尾逗号后的参数声明文本

        # 单条参数尾部分号只表示语句结束，这里要把它从参数正文里剥离出去。
        if allow_trailing_semicolon and str_cleaned.endswith(";"):

            # 清掉结尾分号后，右侧参数值表达式就不会误把结束符当成内容。
            str_cleaned = str_cleaned[:-1].rstrip()  # 去掉单条参数结束符后的声明文本

        # 匹配 parameter/localparam 关键字和剩余声明文本。
        match_param_decl = re.match(r"^(parameter|localparam)\b(?P<remainder>.*)$", str_cleaned)  # 单条参数声明匹配结果

        # 未命中参数关键字时，说明当前文本不是参数声明。
        if not match_param_decl:

            # 不含 parameter/localparam 关键字的文本不进入参数声明结果。
            return None

        # 提取 parameter 或 localparam 关键字。
        str_keyword = match_param_decl.group(1)  # 参数关键字

        # 取出关键字后的声明主体。
        str_remainder = match_param_decl.group("remainder").strip()  # 参数声明剩余文本

        # 剩余文本为空时，说明声明不完整。
        if not str_remainder:

            # 关键字后没有主体时，当前参数声明仍然不完整。
            return None

        # 在剩余文本里定位顶层赋值号位置。
        list_assignment_positions = self._find_procedural_assignment_operators(str_remainder)  # 参数声明里的赋值号位置列表

        # 没有赋值号时，不视为可落地的参数声明。
        if not list_assignment_positions:

            # 缺少顶层赋值号时，当前文本不构成稳定参数声明。
            return None

        # 只取首个顶层赋值号作为参数声明的切分点。
        int_assignment_index = list_assignment_positions[0]  # 参数声明主赋值号位置

        # 左侧文本承载参数名及其声明规格。
        str_lhs = str_remainder[:int_assignment_index].strip()  # 参数声明左侧文本

        # 右侧文本承载参数值表达式。
        str_value = str_remainder[int_assignment_index + 1 :].strip()  # 参数值表达式文本

        # 左右任一为空时，都说明声明还不完整。
        if not str_lhs or not str_value:

            # 参数左右两侧任一缺失时，都不能安全生成 ParamDecl。
            return None

        # 从左侧声明文本末尾提取参数名。
        match_name = re.search(r"([A-Za-z_]\w*)\s*$", str_lhs)  # 参数名匹配结果

        # 左侧提取不到合法标识符时，说明当前文本不能安全解析为参数声明。
        if not match_name:

            # 左侧尾部没有合法标识符时，当前参数名无法可靠提取。
            return None

        # 提取参数名供 ParamDecl 结构继续使用。
        str_name = match_name.group(1)  # 当前参数声明里最终提取出的参数名

        # 参数名前的剩余文本视为 decl_spec。
        str_decl_spec = str_lhs[: match_name.start()].strip()  # 参数声明规格文本

        # 返回结构化的单条参数声明。
        return ParamDecl(str_keyword, str_name, str_value, str_decl_spec, comment)

    # 解析一行内包含多个参数项的 parameter/localparam 声明。
    def _parse_param_decls(
        self,
        text: str,
        comment: str = "",
        *,
        allow_trailing_comma: bool,
        allow_trailing_semicolon: bool,
    ) -> list[ParamDecl]:
        """
        解析一组可能包含多个条目的 parameter 或 localparam 声明。

        参数:
            text: 参数声明组文本。
            comment: 首个参数条目关联的行尾注释。
            allow_trailing_comma: 是否允许声明组尾部保留逗号。
            allow_trailing_semicolon: 是否允许声明组尾部保留分号。
        返回:
            list[ParamDecl]: 解析得到的参数声明列表；失败时返回空列表。
        """

        # 先裁掉两端空白，得到待拆分的参数声明组文本。
        str_cleaned = text.strip()  # 参数声明组主体

        # 参数组尾逗号只承担条目分隔作用，不应参与后续条目分析。
        if allow_trailing_comma and str_cleaned.endswith(","):

            # 先移除参数组尾逗号，避免顶层切分前残留伪条目边界。
            str_cleaned = str_cleaned[:-1].rstrip()  # 去掉尾逗号后的参数声明组文本

        # 参数声明组尾部分号只表示整组结束，这里要避免它混进最后一个条目。
        if allow_trailing_semicolon and str_cleaned.endswith(";"):

            # 把整组结束分号提前剥离，后面的顶层逗号切分才不会污染最后一项。
            str_cleaned = str_cleaned[:-1].rstrip()  # 去掉整组参数结束符后的声明组文本

        # 匹配 parameter/localparam 关键字和后续多项声明主体。
        match_param_group = re.match(r"^(parameter|localparam)\b(?P<remainder>.*)$", str_cleaned, re.DOTALL)  # 参数声明组匹配结果

        # 未命中参数声明组时，回退到单条参数声明解析。
        if not match_param_group:

            # 这里缓存的是单条参数回退解析会复用的尾逗号和尾分号开关。
            dict_fallback_parse_options = {  # 单条参数回退解析的关键字参数
                "allow_trailing_comma": allow_trailing_comma,  # 是否允许尾部逗号
                "allow_trailing_semicolon": allow_trailing_semicolon,  # 是否允许尾部分号
            }

            # 再尝试把整段文本按单条参数声明直接回退解析。
            parsed_param_decl_node = self._parse_param_decl(  # 单条参数声明回退解析结果
                text,  # 当前待回退解析的参数文本
                comment,  # 当前参数文本关联的行尾注释
                **dict_fallback_parse_options,  # 把尾逗号和尾分号兼容开关原样透传给回退解析器
            )

            # 单条回退路径解析成功时返回单元素列表，否则返回空列表。
            return [parsed_param_decl_node] if parsed_param_decl_node is not None else []

        # 提取当前参数声明组的关键字。
        str_keyword = match_param_group.group(1)  # 参数声明组关键字

        # 取出关键字后的多项参数主体。
        str_remainder = match_param_group.group("remainder").strip()  # 参数声明组剩余文本

        # 按顶层逗号切出每个参数条目。
        list_entries = self._split_top_level(str_remainder, ",")  # 参数条目列表

        # 逐项收集解析出的参数声明。
        list_params: list[ParamDecl] = []  # 参数声明组里的结构化参数列表

        # 第一项里的 decl_spec 可以作为后续省略项的共享声明规格。
        str_shared_decl_spec = ""  # 可供后续条目继承的声明规格

        # 顺序解析每个参数条目。
        for int_index, str_entry in enumerate(list_entries):

            # 去掉条目两端空白，得到可直接分析的参数项文本。
            str_candidate = str_entry.strip()  # 当前参数条目文本

            # 参数组里允许重复显式写出 parameter/localparam 关键字。
            match_repeated_keyword = re.match(r"^(parameter|localparam)\b(?P<remainder>.*)$", str_candidate, re.DOTALL)  # 条目级关键字匹配结果

            # 命中重复关键字时，要同步更新当前关键字和条目主体。
            if match_repeated_keyword:

                # 当前条目显式重复关键字时，需要更新后续条目的主关键字。
                str_keyword = match_repeated_keyword.group(1)  # 当前条目显式写出的关键字

                # 保留去掉重复关键字后的条目主体，供后续赋值号判断复用。
                str_candidate = match_repeated_keyword.group("remainder").strip()  # 去掉重复关键字后的条目主体

            # 定位当前条目里的顶层赋值号。
            list_assignment_positions = self._find_procedural_assignment_operators(str_candidate)  # 当前条目赋值号位置列表

            # 没有赋值号时，整组参数声明视为不稳定。
            if not list_assignment_positions:

                # 当前条目缺少顶层赋值号时，整组参数声明整体判定失败。
                return []

            # 取当前条目的首个顶层赋值号位置。
            int_assignment_index = list_assignment_positions[0]  # 当前条目的主赋值号位置

            # 左侧文本承载参数名和声明规格。
            str_lhs = str_candidate[:int_assignment_index].strip()  # 当前条目左侧文本

            # 右侧表达式承载的是当前参数条目的实际默认值文本。
            str_value = str_candidate[int_assignment_index + 1 :].strip()  # 当前条目右侧值表达式

            # 从左侧文本末尾提取参数名。
            match_name = re.search(r"([A-Za-z_]\w*)\s*$", str_lhs)  # 当前条目参数名匹配结果

            # 缺少参数名或值表达式时，整组解析失败。
            if not match_name or not str_value:

                # 条目里缺少参数名或值表达式时，整组结果不再继续拼接。
                return []

            # 条目级 decl_spec 为空时，回退复用第一项的共享声明规格。
            str_decl_spec = str_lhs[: match_name.start()].strip() or str_shared_decl_spec  # 当前条目的声明规格文本

            # 第一项显式携带 decl_spec 时，把它记录给后续条目复用。
            if int_index == 0 and str_decl_spec:

                # 首项声明规格最完整时，把它缓存给后续省略项直接继承。
                str_shared_decl_spec = str_decl_spec  # 供后续条目复用的共享声明规格

            # 追加当前条目解析出的结构化参数声明。
            list_params.append(
                ParamDecl(
                    str_keyword,
                    match_name.group(1),
                    str_value,
                    str_decl_spec,
                    comment if int_index == 0 else "",
                )
            )

        # 返回参数声明组解析得到的全部参数条目。
        return list_params

    # 提取声明前缀里的 leading attributes。
    def _extract_leading_attributes(self, text: str) -> tuple[str, str]:
        """
        提取文本前缀里的 `(* ... *)` attributes，并返回剩余正文。

        参数:
            text: 原始声明文本。
        返回:
            tuple[str, str]: attributes 前缀文本和去掉 attributes 后的剩余正文。
        """

        # 匹配前缀 attributes 和剩余正文两部分。
        match_attributes = re.match(r"^((?:\(\*.*?\*\)\s*)*)(.*)$", text.strip())  # leading attributes 匹配结果

        # 匹配失败时，按无 attributes 处理原始文本。
        if not match_attributes:

            # 没有 attributes 前缀时，正文直接沿用原始声明文本。
            return "", text.strip()

        # 返回裁剪后的 attributes 文本和剩余正文。
        return match_attributes.group(1).strip(), match_attributes.group(2).strip()

    # 提取文本内联块注释，并返回去掉注释后的正文。
    def _extract_inline_block_comments(self, text: str) -> tuple[str, str]:
        """
        提取文本里的内联块注释，并返回无块注释正文和注释合集。

        参数:
            text: 原始声明文本。
        返回:
            tuple[str, str]: 去掉块注释后的正文，以及用空格拼接后的块注释文本。
        """

        # 收集原文里的全部内联块注释。
        list_comments = re.findall(r"/\*.*?\*/", text)  # 原文里的块注释列表

        # 移除块注释后得到可继续语法解析的正文。
        str_stripped = re.sub(r"/\*.*?\*/", "", text).strip()  # 去掉块注释后的正文

        # 返回正文和拼接后的块注释文本。
        return str_stripped, " ".join(str_comment.strip() for str_comment in list_comments)

    # 解析单条模块端口声明文本。
    def _parse_port_line(
        self,
        raw: str,
        inline_comment: str,
        current_group: str,
        current_section: str,
        current_subgroup: str,
    ) -> PortDecl | None:
        """
        解析单条 input/output/inout 端口声明。

        参数:
            raw: 原始端口声明文本。
            inline_comment: 当前端口声明关联的行尾注释。
            current_group: 当前端口所属分组名。
            current_section: 当前端口所属 section 名。
            current_subgroup: 当前端口所属 subgroup 名。
        返回:
            PortDecl | None: 解析成功时返回结构化端口声明，否则返回 None。
        """

        # 先提取端口声明前缀里的 attributes 文本。
        str_attributes, str_cleaned = self._extract_leading_attributes(raw)  # 端口声明 attributes 与去前缀正文

        # 再去掉端口正文里的内联块注释和尾部分隔符。
        str_cleaned, str_block_comment = self._extract_inline_block_comments(str_cleaned.rstrip(",;"))  # 去块注释后的端口正文与块注释文本

        # 缺省时沿用行尾注释；没有行尾注释时回退到当前 section 名。
        str_comment = inline_comment or current_section  # 当前端口声明的基础注释文本

        # 命中块注释时，把它拼到端口注释末尾保留原语义。
        if str_block_comment:

            # 把块注释追加到当前端口注释末尾，避免注释语义丢失。
            str_comment = f"{str_comment} {str_block_comment}".strip()  # 合并块注释后的端口注释文本

        # 按方向、signed、位宽、名称和 unpacked 维度匹配单条端口声明。
        str_port_decl_pattern = (
            r"^(input|output|inout)\s+"
            r"(?:(?:wire|reg|logic)\s+)?"
            r"(signed\s*)?"
            r"(\[[^\]]+\])?\s*"
            r"(\w+)"
            r"((?:\s*\[[^\]]+\])*)"
            r"\s*(?:=\s*.+)?$"
        )  # 单条端口声明匹配正则

        # 用前面拼好的端口正则检查当前文本能否稳定还原为单条端口声明。
        match_port_decl = re.match(str_port_decl_pattern, str_cleaned)  # 单条端口声明匹配结果

        # 当前文本不是可识别端口声明时，返回 None 交给上层继续兜底。
        if not match_port_decl:

            # 当前端口文本未命中结构化端口声明模式。
            return None

        # 返回结构化端口声明，供后续布局和渲染流程继续复用。
        return PortDecl(
            direction=match_port_decl.group(1),
            width=(match_port_decl.group(3) or "").strip(),
            name=match_port_decl.group(4),
            comment=str_comment,
            group=current_group,
            section=current_section,
            signed=bool(match_port_decl.group(2)),
            unpacked=(match_port_decl.group(5) or "").strip(),
            attributes=str_attributes,
            subgroup=current_subgroup,
        )

    # 解析单条信号声明文本，并在需要时拆成多个 SignalDecl。
    def _parse_signal_decl(self, raw: str, inline_comment: str) -> list[SignalDecl]:
        """
        解析 `wire/reg/logic/integer/real` 信号声明文本。

        参数:
            raw: 原始信号声明文本。
            inline_comment: 当前信号声明关联的行尾注释。
        返回:
            list[SignalDecl]: 解析得到的单个或多个信号声明。
        异常:
            VerilogFormatterError: 声明形态不稳定或 real 声明不符合归一化约束时抛出。
        """

        # 先裁掉尾部分号，再提取声明前缀里的 attributes 文本。
        str_attributes, str_cleaned = self._extract_leading_attributes(raw.strip().rstrip(";").strip())  # 信号声明 attributes 与去前缀正文

        # 再去掉正文里的内联块注释，保留后续可复用的块注释文本。
        str_cleaned, str_block_comment = self._extract_inline_block_comments(str_cleaned)  # 去块注释后的信号正文与块注释文本

        # 信号声明默认沿用行尾注释作为首个信号的注释文本。
        str_comment = inline_comment  # 当前信号声明的基础注释文本

        # 命中块注释时，把它拼到当前信号注释末尾。
        if str_block_comment:

            # 把块注释追加到信号注释末尾，避免声明语义丢失。
            str_comment = f"{str_comment} {str_block_comment}".strip()  # 合并块注释后的信号注释文本

        # 先拆出声明种类、signed 标记、位宽和剩余信号列表文本。
        str_signal_decl_pattern = (
            r"^(wire|tri1|reg|logic|integer|real)\s*"
            r"(signed\s*)?"
            r"(\[[^\]]+\])?\s*"
            r"(.+)$"
        )  # 单条信号声明匹配正则

        # 用前面拼好的信号正则抽取声明种类、位宽和剩余条目文本。
        match_signal_decl = re.match(str_signal_decl_pattern, str_cleaned)  # 单条信号声明匹配结果

        # 匹配失败时，说明当前声明还不是可安全归一化的形态。
        if not match_signal_decl:

            # 抛出统一的信号声明归一化异常。
            raise VerilogFormatterError(
                "> ERR: [Python] signal declaration normalization failed."
            ) from self._strict_error(
                "declaration_normalization_violation",
                raw,
                "Rewrite the declaration into one or more single-signal forms such as "
                "'reg signed [W-1:0] name [0:N-1];'.",
            )

        # 提取当前声明的种类关键字。
        str_kind = match_signal_decl.group(1)  # 信号声明种类

        # 记录当前声明是否显式带有 signed 标记。
        bool_signed = bool(match_signal_decl.group(2))  # 当前信号声明的 signed 标记

        # 取出统一作用于本条声明的位宽文本。
        str_width = (match_signal_decl.group(3) or "").strip()  # 当前信号声明的统一位宽文本

        # 取出种类关键字之后的信号列表正文。
        str_remainder = match_signal_decl.group(4).strip()  # 当前信号声明的剩余信号列表文本

        # real 声明不允许额外带 signed 或 packed 位宽信息。
        if str_kind == "real" and (bool_signed or str_width):

            # 抛出 real 声明形态不受支持的统一异常。
            raise VerilogFormatterError(
                "> ERR: [Python] real declaration normalization failed."
            ) from self._strict_error(
                "declaration_normalization_violation",
                raw,
                "Rewrite real declarations into a single-signal form such as "
                "'real gain;' or 'real coeff[0:N-1];'.",
            )

        # 按顶层逗号切出信号声明里的每个条目。
        list_items = self._split_top_level(str_remainder, ",")  # 信号声明条目列表

        # 没有条目时，说明声明仍然不具备安全拆分边界。
        if not list_items:

            # 抛出空条目列表导致的归一化异常。
            raise VerilogFormatterError(
                "> ERR: [Python] signal declaration normalization failed."
            ) from self._strict_error(
                "declaration_normalization_violation",
                raw,
                "Rewrite the declaration into one or more single-signal forms such as "
                "'reg signed [W-1:0] name [0:N-1];'.",
            )

        # real 声明必须保持单信号形态，不能一行声明多个 real。
        if str_kind == "real" and len(list_items) > 1:

            # 抛出多 real 条目不受支持的统一异常。
            raise VerilogFormatterError(
                "> ERR: [Python] real declaration normalization failed."
            ) from self._strict_error(
                "declaration_normalization_violation",
                raw,
                "Rewrite real declarations into a single-signal form such as "
                "'real gain;' or 'real coeff[0:N-1];'.",
            )

        # 收集拆分后的结构化信号声明。
        list_signal_decls: list[SignalDecl] = []  # 当前声明拆分后的信号声明列表

        # 匹配每个条目里的名称、unpacked 维度和可选初始化表达式。
        pattern_signal_item: re.Pattern[str] = re.compile(r"^(\w+)" r"((?:\s*\[[^\]]+\])*)" r"\s*(?:=\s*(.+))?$")  # 单个信号条目匹配模式

        # 逐个条目恢复成 SignalDecl。
        for int_index, str_item in enumerate(list_items):

            # 去掉条目两端空白后，再做单条信号条目匹配。
            match_item = pattern_signal_item.match(str_item.strip())  # 单个信号条目匹配结果

            # 当前条目不合法时，整条声明不能继续安全拆分。
            if not match_item:

                # 抛出单个信号条目形态不稳定的统一异常。
                raise VerilogFormatterError(
                    "> ERR: [Python] signal declaration normalization failed."
                ) from self._strict_error(
                    "declaration_normalization_violation",
                    raw,
                    "Rewrite the declaration into one or more single-signal forms such as "
                    "'reg signed [W-1:0] name [0:N-1];'.",
                )

            # 提取当前信号条目的 unpacked 维度文本。
            str_unpacked = (match_item.group(2) or "").strip()  # 当前信号条目的 unpacked 维度文本

            # 提取当前条目尾部的初始化表达式，供单信号声明复用。
            str_init = (match_item.group(3) or "").strip()  # 当前信号条目的初始化表达式文本

            # 追加当前条目还原得到的结构化信号声明。
            list_signal_decls.append(
                SignalDecl(
                    kind=str_kind,
                    width=str_width,
                    name=match_item.group(1),
                    init=str_init,
                    comment=str_comment if int_index == 0 else "",
                    signed=bool_signed,
                    unpacked=str_unpacked,
                    attributes=str_attributes,
                    leading_comments=[],
                )
            )

        # 返回拆分后的结构化信号声明列表。
        return list_signal_decls

    # 把 packed 位宽文本归类成 single、multi 或 unknown。
    def _classify_declared_width(self, width: str) -> str:
        """
        根据 packed 位宽文本判断信号是单比特、多比特还是未知宽度。

        参数:
            width: 原始 packed 位宽文本。
        返回:
            str: `single`、`multi` 或 `unknown`。
        """

        # 没有位宽文本时，按单比特声明处理。
        if not width:

            # 空位宽文本默认表示单比特信号。
            return "single"

        # 统一位宽规格里的空白，方便后续判断边界。
        str_normalized = self._normalize_decl_spec_spacing(width).strip()  # 规范化后的位宽文本

        # 不是标准方括号包裹形态时，无法稳定判断位宽类别。
        if not str_normalized.startswith("[") or not str_normalized.endswith("]"):

            # 非标准 packed 位宽文本统一按 unknown 处理。
            return "unknown"

        # 去掉外层方括号，只保留位宽载荷正文。
        str_payload = str_normalized[1:-1].strip()  # packed 位宽内部载荷文本

        # 在载荷里定位顶层冒号位置。
        int_colon_index = self._find_top_level_colon(str_payload)  # 位宽载荷里的顶层冒号位置

        # 缺少顶层冒号时，无法按 `[msb:lsb]` 形式判断位宽。
        if int_colon_index == -1:

            # 不满足标准 packed 位宽格式时返回 unknown。
            return "unknown"

        # 提取冒号左侧的上界文本。
        str_left = str_payload[:int_colon_index].strip()  # 位宽上界文本

        # 提取冒号右侧的下界文本。
        str_right = str_payload[int_colon_index + 1 :].strip()  # 位宽下界文本

        # 只有纯数字上下界才允许直接静态判断 single/multi。
        if not re.fullmatch(r"\d+", str_left) or not re.fullmatch(r"\d+", str_right):

            # 动态表达式位宽无法静态比较，统一返回 unknown。
            return "unknown"

        # 左右界相等时视为单比特，否则视为多比特。
        return "single" if int(str_left) == int(str_right) else "multi"

    # 根据当前缓存的位宽分类表查询指定信号的位宽类别。
    def _classify_signal_width(self, name: str) -> str:
        """
        查询当前信号位宽分类缓存中的指定信号。

        参数:
            name: 待查询的信号名。
        返回:
            str: 当前信号的位宽分类；缺省时返回 `unknown`。
        """

        # 从当前信号位宽分类缓存里读取指定信号的分类结果。
        return self._current_signal_width_classes.get(name, "unknown")

    # 为端口和信号声明构建统一的位宽分类映射表。
    def _build_signal_width_class_map(self, ports: list[PortDecl], decls: list[SignalDecl]) -> dict[str, str]:
        """
        汇总端口和信号声明的位宽信息，生成名称到位宽类别的映射。

        参数:
            ports: 已解析端口声明列表。
            decls: 已解析信号声明列表。
        返回:
            dict[str, str]: 信号名到 `single`、`multi` 或 `unknown` 的映射表。
        """

        # 收集端口和信号声明的位宽分类结果。
        dict_width_classes: dict[str, str] = {}  # 信号名到位宽分类的映射表

        # 先把端口声明里的位宽分类写入缓存表。
        for port_decl in ports:

            # raw_text 端口或缺少名字的端口不进入静态位宽分类表。
            if not port_decl.name or port_decl.raw_text:

                # 当前端口不具备稳定静态分类条件时，直接跳过。
                continue

            # 为当前端口记录 packed 位宽分类结果。
            dict_width_classes[port_decl.name] = self._classify_declared_width(port_decl.width)  # 当前端口的位宽分类

        # 再把普通信号声明里的位宽分类写入缓存表。
        for signal_decl in decls:

            # assign 伪声明或无名声明不参与位宽分类缓存构建。
            if not signal_decl.name or signal_decl.kind in {"assign", "__assign__"}:

                # 当前声明不属于可缓存位宽类别的普通信号声明。
                continue

            # integer/real 且不带位宽时，只能保守记录为 unknown。
            if signal_decl.kind in {"integer", "real"} and not signal_decl.width:

                # 只在当前名字尚未出现时补入保守的 unknown 分类。
                dict_width_classes.setdefault(signal_decl.name, "unknown")

                # 当前声明已经完成保守分类，无需继续走 packed 位宽路径。
                continue

            # 为当前普通信号声明记录 packed 位宽分类结果。
            dict_width_classes[signal_decl.name] = self._classify_declared_width(signal_decl.width)  # 当前信号的位宽分类

        # 返回汇总后的位宽分类映射表。
        return dict_width_classes

    # 从 always 头部拆出敏感列表头和尾随正文。
    def _split_always_header(self, text: str) -> tuple[str, str]:
        """
        拆分 always 语句头中的敏感列表和尾随正文。

        参数:
            text: 原始 always 语句头文本。
        返回:
            tuple[str, str]: 规范化后的 always 头部，以及紧随其后的剩余文本。
        异常:
            VerilogFormatterError: always 头部不完整或敏感列表不平衡时抛出。
        """

        # 去掉头尾空白，得到待拆分的 always 头文本。
        str_working = text.strip()  # 去空白后的 always 头文本

        # 只有 always 起始的文本才允许走专用拆分路径。
        if not str_working.startswith("always"):

            # 抛出 always 头部起始关键字不合法的统一异常。
            raise VerilogFormatterError("> ERR: [Python] always header normalization failed.") from self._strict_error(
                "unsupported_shape",
                text,
                "Use a stable always block header before formatting.",
            )

        # 去掉 always 关键字后，保留敏感列表和尾随正文。
        str_tail = str_working[len("always") :].lstrip()  # always 关键字后的剩余文本

        # always 后必须紧跟 @ 敏感列表起始符号。
        if not str_tail.startswith("@"):

            # 抛出 always 缺失敏感列表起始符的统一异常。
            raise VerilogFormatterError("> ERR: [Python] always header normalization failed.") from self._strict_error(
                "unsupported_shape",
                text,
                "Use a stable always block header before formatting.",
            )

        # 去掉 @ 之后的空白，准备识别 * 或括号敏感列表。
        str_tail = str_tail[1:].lstrip()  # 去掉 @ 后的敏感列表文本

        # always @* 形式可以直接拆出固定头部和尾随正文。
        if str_tail.startswith("*"):

            # 返回标准化的 always@(*) 头和余下正文。
            return "always@(*)", str_tail[1:].strip()

        # 非 @* 形式必须以括号包裹敏感列表。
        if not str_tail.startswith("("):

            # 抛出敏感列表括号缺失的统一异常。
            raise VerilogFormatterError(
                "> ERR: [Python] always sensitivity list normalization failed."
            ) from self._strict_error(
                "unsupported_shape",
                text,
                "Balance the always sensitivity list before formatting.",
            )

        # 记录敏感列表括号嵌套深度，直到找到最外层闭合位置。
        int_depth = 0  # 敏感列表括号嵌套深度

        # 初始化敏感列表闭合括号下标，缺省时保持未命中。
        int_close_index = -1  # 最外层右括号下标

        # 扫描敏感列表文本，定位与首个左括号配对的闭合位置。
        for int_index, str_char in enumerate(str_tail):

            # 左括号会让当前敏感列表嵌套深度增加。
            if str_char == "(":

                # 进入更深一层括号嵌套。
                int_depth += 1  # 进入括号后的敏感列表嵌套深度

            # 右括号会尝试关闭当前最内层括号。
            elif str_char == ")":

                # 当前字符让括号嵌套深度回退一层。
                int_depth -= 1  # 遇到右括号后的敏感列表嵌套深度

                # 回到最外层时，说明敏感列表边界已经闭合。
                if int_depth == 0:

                    # 记录最外层闭合括号位置并停止继续扫描。
                    int_close_index = int_index  # 最外层敏感列表闭合位置

                    # 命中最外层闭合位置后，不再继续扫描后续字符。
                    break

        # 没有找到最外层闭合右括号时，敏感列表仍然不完整。
        if int_close_index == -1:

            # 抛出敏感列表括号不平衡的统一异常。
            raise VerilogFormatterError(
                "> ERR: [Python] always sensitivity list normalization failed."
            ) from self._strict_error(
                "unsupported_shape",
                text,
                "Balance the always sensitivity list before formatting.",
            )

        # 组装规范化后的 always 敏感列表头。
        str_header = f"always@{str_tail[: int_close_index + 1]}"  # 规范化后的 always 头部

        # 返回标准化头部和尾随正文。
        return str_header, str_tail[int_close_index + 1 :].strip()

    # 判断语句文本是否已经在顶层遇到分号结束。
    def _statement_has_top_level_semicolon(self, text: str) -> bool:
        """
        判断给定语句文本是否在顶层结构上已经出现结束分号。

        参数:
            text: 待检查的语句文本。
        返回:
            bool: 顶层存在分号时返回 True，否则返回 False。
        """

        # 记录括号、方括号和花括号的综合嵌套深度。
        int_depth = 0  # 当前语句文本的综合嵌套深度

        # 顺序扫描语句文本中的每个字符。
        for str_char in text:

            # 左侧定界符会让综合嵌套深度增加。
            if str_char in "([{":

                # 进入更深一层复合结构。
                int_depth += 1  # 当前字符让综合嵌套深度向内推进一层

            # 右侧定界符会让综合嵌套深度向外回退。
            elif str_char in ")]}":

                # 防止异常文本把深度减到负数。
                int_depth = max(0, int_depth - 1)  # 当前字符让综合嵌套深度向外回退一层

            # 只有位于顶层的分号才表示语句结束。
            elif str_char == ";" and int_depth == 0:

                # 已经识别到顶层结束分号，可以提前返回。
                return True

        # 整段文本扫描完成后仍未找到顶层分号。
        return False

    # 从按行切分的片段里回收一条完整语句文本。
    def _collect_statement_text_from_fragments(self, fragments: list[str], start: int) -> tuple[str, int]:
        """
        从语句片段列表中收集一条以顶层分号结束的完整语句。

        参数:
            fragments: 已按物理片段切分的语句文本列表。
            start: 语句起始片段下标。
        返回:
            tuple[str, int]: 拼接后的完整语句文本，以及下一条片段的起始下标。
        """

        # 先把起始片段规范化后放入语句收集缓冲区。
        list_statement_lines = [self._normalize_statement_line(fragments[start].strip())]  # 当前语句片段缓冲区

        # 下一轮从起始片段之后继续向前拼接。
        int_index = start + 1  # 待检查的下一个片段下标

        # 持续向后收集片段，直到遇到顶层结束分号。
        while int_index < len(fragments):

            # 把当前已收集的非空片段重新拼成完整语句文本。
            str_joined = "\n".join(line for line in list_statement_lines if line)  # 当前已拼接语句文本

            # 已经命中顶层结束分号时，当前语句收集完成。
            if self._statement_has_top_level_semicolon(str_joined):

                # 结束当前语句拼接，保留 int_index 作为下一段起点。
                break

            # 继续把下一片段规范化后并入当前语句缓冲区。
            list_statement_lines.append(
                self._normalize_statement_line(fragments[int_index].strip())
            )

            # 当前片段已经消费，继续检查后续片段。
            int_index += 1  # 继续检查后续片段的下标位置

        # 返回拼接后的语句文本和下一个待处理片段下标。
        return "\n".join(line for line in list_statement_lines if line), int_index

    # 从物理行列表中回收一条声明语句及其首个行尾注释。
    def _collect_declaration_statement(self, lines: list[str], start: int) -> tuple[str, str, int]:
        """
        从多行声明中提取完整声明文本、首个行尾注释和结束下标。

        参数:
            lines: 原始物理行列表。
            start: 声明起始行下标。
        返回:
            tuple[str, str, int]: 拼接后的声明文本、首个行尾注释和下一行下标。
        """

        # 收集当前多行声明中的有效代码行。
        list_statement_lines: list[str] = []  # 当前声明的代码行缓冲区

        # 只记录当前声明遇到的首个行尾注释。
        str_comment = ""  # 当前声明绑定的首个行尾注释

        # 从声明起始行开始逐行向后收集。
        int_index = start  # 当前正在消费的物理行下标

        # 持续回收物理行，直到声明在顶层遇到分号结束。
        while int_index < len(lines):

            # 拆出当前物理行的代码部分和行尾注释。
            str_raw, str_inline_comment = self._split_comment(lines[int_index].strip())  # 当前行代码与行尾注释

            # 一旦遇到第一条行尾说明，就把它作为整条多行声明的代表注释。
            if str_inline_comment and not str_comment:

                # 这里保留的是当前多行声明最先出现且最贴近语义的行尾注释。
                str_comment = str_inline_comment  # 当前声明要沿用的首条行尾说明

            # 当前行仍含有效代码时，才并入声明文本。
            if str_raw.strip():

                # 把当前有效代码行追加到声明缓冲区。
                list_statement_lines.append(str_raw.strip())

            # 重新拼接当前声明，判断是否已经命中顶层分号。
            str_joined = " ".join(line for line in list_statement_lines if line)  # 当前声明的拼接文本

            # 顶层分号出现后，这条声明已经完整。
            if self._statement_has_top_level_semicolon(str_joined):

                # 返回完整声明文本、首个注释以及下一条起始下标。
                return str_joined, str_comment, int_index + 1

            # 当前行还未闭合整条声明时，继续向后推进。
            int_index += 1  # 继续收集下一条物理行并更新片段下标

        # 到达文件尾仍未闭合时，返回当前已收集的保守结果。
        return " ".join(line for line in list_statement_lines if line), str_comment, int_index

    # 从片段列表中回收一个原始 case...endcase 语句块。
    def _collect_raw_case_statement_from_fragments(self, fragments: list[str], start: int) -> tuple[list[str], int]:
        """
        从片段列表中提取一个保持原状的 case 语句块。

        参数:
            fragments: 已按片段切分的语句文本列表。
            start: case 语句块起始片段下标。
        返回:
            tuple[list[str], int]: 收集到的 case 片段列表，以及下一条片段的起始下标。
        异常:
            VerilogFormatterError: 找不到对应 endcase 时抛出。
        """

        # 顺序收集 case 语句块里的规范化片段。
        list_collected: list[str] = []  # 当前 case 语句块的规范化片段列表

        # 记录 case/casez/casex 与 endcase 的嵌套层级。
        int_depth = 0  # 当前 case 语句块嵌套深度

        # 从起始片段开始继续向后消费。
        int_index = start  # 当前 case 片段下标

        # 持续扫描片段，直到最外层 case 块闭合。
        while int_index < len(fragments):

            # 规范化当前片段文本，便于统一判断 case 边界。
            str_normalized = self._normalize_statement_line(fragments[int_index].strip())  # 当前规范化片段文本

            # 只有非空片段才会参与 case 嵌套层级统计。
            if str_normalized:

                # 把当前有效片段并入 case 收集结果。
                list_collected.append(str_normalized)

                # 深度 helper 根据 case 起点或 endcase 更新嵌套层级。
                int_depth += self._raw_case_depth_delta(str_normalized)  # 当前片段处理后的 case 嵌套深度

                # 回到最外层时，说明整个 case 块已经闭合。
                if int_depth == 0:

                    # 返回完整的 case 片段列表和下一条起始下标。
                    return list_collected, int_index + 1

            # 当前片段处理完成后继续向后扫描。
            int_index += 1  # 继续扫描下一个 case 片段

        # 扫描到末尾仍未闭合时，当前 case 结构不完整。
        raise VerilogFormatterError("> ERR: [Python] case block normalization failed.") from self._strict_error(
            "unsupported_shape",
            fragments[start].strip(),
            "Close each case block with endcase before formatting.",
        )

    # raw case 深度 helper 只识别 case 家族起点和 endcase 终点。
    def _raw_case_depth_delta(self, str_normalized: str) -> int:
        """返回规范化片段对 case 嵌套深度的影响。

        参数:
            str_normalized: 当前规范化后的语句片段。
        返回:
            case 起点返回 1，endcase 返回 -1，其它片段返回 0。
        """

        # case、casez 和 casex 都打开一层 raw case 结构。
        if re.match(r"^(?:case|casez|casex)\b", str_normalized):

            # 调用方据此增加嵌套深度。
            return 1

        # endcase 关闭最近一层 raw case 结构。
        if str_normalized.startswith("endcase"):

            # 调用方据此减少嵌套深度。
            return -1

        # 普通片段不影响 case 层级。
        return 0

    # 按顶层分号把声明区域拆成独立语句。
    def _split_declaration_statements(self, text: str) -> list[str]:
        """
        按顶层分号切分声明文本，并为每段恢复结尾分号。

        参数:
            text: 待拆分的声明区域文本。
        返回:
            list[str]: 逐条恢复后的声明语句列表。
        """

        # 逐条收集补回分号后的声明语句文本。
        list_statements: list[str] = []  # 拆分后的声明语句列表

        # 顶层分号切分后，逐段清理空白并恢复语句结束符。
        for str_entry in self._split_top_level(text, ";"):

            # 去掉当前声明片段首尾空白，判断是否仍有有效内容。
            str_stripped = str_entry.strip()  # 当前声明片段的去空白文本

            # 空片段不需要恢复成声明语句。
            if str_stripped:

                # 为当前有效声明片段补回结束分号。
                list_statements.append(f"{str_stripped};")

        # 返回拆分后的独立声明语句列表。
        return list_statements

    # 解析 ANSI 风格模块头中的端口声明列表。
    def _parse_ports(self, text: str, macro_expansions: dict[str, str] | None = None) -> list[PortDecl]:
        """
        解析 ANSI 风格模块头中的端口声明列表。

        参数:
            text: 端口区域原始文本。
            macro_expansions: 端口宏展开映射。
        返回:
            list[PortDecl]: 解析得到的端口声明列表。
        异常:
            VerilogFormatterError: 端口声明不是单条稳定形态时抛出。
        """

        # 纯空端口区不产生任何结构化端口声明。
        if not text.strip():

            # 空端口区直接返回空端口列表。
            return []

        # 端口区宏展开通常来自模块头上下文，这里先兜底为空映射。
        dict_macro_expansions = macro_expansions or {}  # 端口解析使用的宏展开映射

        # 收集端口区里解析得到的结构化端口声明。
        list_ports: list[PortDecl] = []  # 端口声明结果列表

        # 先建立 banner 状态机里的大分组状态。
        str_current_group = ""  # 当前端口分组标题

        # 再建立给后续端口补充中层语义的 banner 小节状态。
        str_current_section = ""  # 当前端口小节标题

        # 最后建立仅影响端口细粒度归类的 banner 子分组状态。
        str_current_subgroup = ""  # 当前端口的细粒度子分组标题

        # 累积只包含属性而没有实际声明的前置 attributes。
        str_pending_attributes = ""  # 待拼接到下一条端口声明的前置属性

        # 按物理行顺序解析端口区域，保留注释和宏展开边界。
        for str_raw_line in text.splitlines():

            # 去掉当前物理行头尾空白，统一判断分支入口。
            str_stripped = str_raw_line.strip()  # 当前端口物理行的规范化文本

            # 空白行只起分隔作用，不产生端口节点。
            if not str_stripped:

                # 当前物理行不承载端口信息，直接跳过。
                continue

            # 宏调用行需要同时保留 raw 节点和可展开的合成端口。
            if str_stripped.startswith("`"):

                # 原样保留端口宏调用行，避免丢失用户显式端口宏。
                list_ports.append(PortDecl("raw", "", "", raw_text=str_stripped))

                # 提取宏名，便于读取源码中缓存的端口宏展开文本。
                str_macro_name = str_stripped.lstrip("`").split(None, 1)[0]  # 当前端口宏名

                # 查找当前宏调用对应的展开文本。
                str_expansion = dict_macro_expansions.get(str_macro_name, "")  # 当前端口宏展开文本

                # 命中可展开文本时，递归生成合成端口节点。
                if str_expansion:

                    # 追加展开后解析出的端口，但过滤掉其中的 raw 宏占位节点。
                    list_ports.extend(
                        port_decl
                        for port_decl in self._parse_ports(str_expansion, {})
                        if not port_decl.raw_text
                    )

                # 当前宏调用行已经完整处理。
                continue

            # 行注释可能承载端口分组、小节或子分组信息。
            if str_stripped.startswith("//"):

                # 提取 banner 标题文本，供分组状态机继续判断。
                str_comment_text = extract_banner_title(str_stripped)  # 当前注释行的标题文本

                # 无法提取标题的注释行不改变任何端口分组状态。
                if not str_comment_text:

                    # 普通说明注释不属于端口分组状态输入。
                    continue

                # banner 分隔线会切换当前端口分组标题。
                if is_banner_line(str_stripped):

                    # 进入新的端口分组后，同时清空旧小节和子分组。
                    str_current_group = str_comment_text  # 当前 banner 归属的新端口分组

                    # 进入新分组后，需要先清空上一层遗留的小节状态。
                    str_current_section = ""  # 进入新分组后的当前端口小节标题

                    # 进入新分组后，也要同步清空上一层遗留的子分组状态。
                    str_current_subgroup = ""  # 进入新分组后的当前端口子分组标题

                    # 当前注释已经切换分组状态，不再进入后续分支。
                    continue

                # 标准小节标签会更新当前端口小节状态。
                if self._is_port_section_label(str_comment_text):

                    # 切换端口小节时，需要同步清空旧子分组。
                    str_current_section = str_comment_text  # 当前 banner 归属的新端口小节

                    # 切换小节后，旧子分组已经不再适用。
                    str_current_subgroup = ""  # 切换小节后的当前端口子分组标题

                    # 当前注释已经切换小节状态，不再继续判断后续标签。
                    continue

                # 这里识别的是不改动大分组、只补充局部标签的子分组标题。
                if self._is_port_subgroup_label(str_comment_text, str_current_group):

                    # 这里只更新后续端口使用的子分组标签，不改动更高层级状态。
                    str_current_subgroup = str_comment_text  # 当前端口要继承的子分组标题

                    # 当前注释已经切换子分组状态，不再进入后续标题逻辑。
                    continue

                # 总线或接口类标题应被视作新的大分组边界。
                if "总线" in str_comment_text or "接口" in str_comment_text:

                    # 更宽泛的接口标题进入新的端口分组层级。
                    str_current_group = str_comment_text  # 接口或总线类标题对应的大分组

                    # 接口级标题进入新的大分组后，需要清空旧小节状态。
                    str_current_section = ""  # 接口或总线类标题后的当前端口小节标题

                    # 接口级标题进入新的大分组后，也要清空旧子分组状态。
                    str_current_subgroup = ""  # 接口或总线类标题后的当前端口子分组标题

                # 其他标题按小节说明处理即可。
                else:

                    # 非总线类标题继续作为当前端口小节描述。
                    str_current_section = str_comment_text  # 普通标题对应的小节说明

                    # 切换到普通小节后，不再沿用之前的子分组。
                    str_current_subgroup = ""  # 普通标题后的当前端口子分组标题

                # 当前注释行只负责维护端口分组状态。
                continue

            # 把当前端口行拆成代码部分和行尾注释。
            str_raw, str_inline_comment = self._split_comment(str_stripped)  # 当前端口行代码与行尾注释

            # 提取前置 attributes，并判断是否仍缺少真实端口声明正文。
            str_attributes, str_candidate = self._extract_leading_attributes(str_raw)  # 当前端口行属性与候选正文

            # 只有 attributes 而没有正文时，需要延迟拼接到下一行声明。
            if str_attributes and not str_candidate:

                # 累积当前行属性，等待后续端口声明一起解析。
                str_pending_attributes = f"{str_pending_attributes} {str_attributes}".strip()  # 累积到下一条端口声明前的属性文本

                # 当前行仅承载属性，等待下一条真实端口声明再一起解析。
                continue

            # 前面已经缓存过独立 attributes 时，需要先拼回当前端口行。
            if str_pending_attributes:

                # 把前置 attributes 还原到当前真实端口声明前面。
                str_raw = f"{str_pending_attributes} {str_raw}".strip()  # 拼回属性后的完整端口声明文本

                # 到这里说明缓存属性已经成功并入真实端口声明正文。
                str_pending_attributes = ""  # 当前属性缓存已经消费完成

            # 先把当前端口代码折叠成单行文本，便于端口解析器复用。
            str_port_text = str_raw.replace("\n", " ").strip()  # 传给端口解析器的单行端口文本

            # 先把当前端口所在的分组上下文打包，避免参数列表过长。
            tuple_port_context = (str_current_group, str_current_section, str_current_subgroup)  # 当前端口的分组上下文

            # 再把单行端口文本连同分组上下文交给端口解析器。
            port_decl_node = self._parse_port_line(  # 当前端口声明解析结果
                str_port_text, str_inline_comment, *tuple_port_context  # 当前端口文本、注释和分组上下文
            )

            # 端口行无法还原为单条稳定声明时，必须要求用户先规范化输入。
            if not port_decl_node:

                # 抛出端口声明形态不稳定的统一异常。
                raise VerilogFormatterError(
                    "> ERR: [Python] port declaration normalization failed."
                ) from self._strict_error(
                    "unsupported_shape",
                    str_stripped,
                    "Rewrite each port as a single input/output/inout declaration "
                    "before formatting.",
                )

            # 当前端口声明解析成功后并入端口结果列表。
            list_ports.append(port_decl_node)

        # 返回模块头端口区解析得到的全部端口声明。
        return list_ports

    # 解析模块头端口区，并在需要时回退到非 ANSI 端口模式。
    def _parse_module_ports(
        self, header_text: str, body: str, macro_expansions: dict[str, str] | None = None
    ) -> tuple[list[PortDecl], str]:
        """
        解析模块头中的端口区域，必要时回退到非 ANSI 端口声明方案。

        参数:
            header_text: 模块头中的端口文本。
            body: 模块主体正文。
            macro_expansions: 端口宏展开映射。
        返回:
            tuple[list[PortDecl], str]: 端口声明列表，以及必要时剥离过端口声明的模块主体。
        异常:
            VerilogFormatterError: ANSI 和非 ANSI 两条端口解析路径都无法稳定恢复时抛出。
        """

        # 纯空端口头表示当前模块没有显式端口。
        if not header_text.strip():

            # 空端口头直接返回空端口列表和原始主体。
            return [], body

        # 优先按 ANSI 风格端口区直接解析。
        try:

            # ANSI 端口解析成功时，主体正文无需改写。
            return self._parse_ports(header_text, macro_expansions), body

        # ANSI 风格失败后，再尝试回退到非 ANSI 端口模式。
        except VerilogFormatterError:

            # 先从模块头里提取裸端口名列表。
            list_bare_names = self._parse_non_ansi_port_names(header_text)  # 非 ANSI 端口名列表

            # 模块头里没有稳定裸端口名时，沿用原始异常即可。
            if not list_bare_names:

                # 当前模块头既非 ANSI 也不是可恢复的非 ANSI 形式。
                raise

            # 使用主体里的 input/output/inout 声明恢复非 ANSI 端口信息。
            return self._parse_non_ansi_ports_from_body(list_bare_names, body)

    # 从非 ANSI 模块头里提取裸端口名列表。
    def _parse_non_ansi_port_names(self, text: str) -> list[str]:
        """
        提取非 ANSI 模块头中的裸端口名列表。

        参数:
            text: 非 ANSI 模块头端口文本。
        返回:
            list[str]: 按出现顺序提取出的裸端口名列表；不匹配时返回空列表。
        """

        # 收集去掉注释后的裸端口片段文本。
        list_fragments: list[str] = []  # 非 ANSI 端口片段列表

        # 逐行读取模块头，只保留真实端口名片段。
        for str_raw_line in text.splitlines():

            # 去掉每行头尾空白，便于识别空行和行注释。
            str_stripped = str_raw_line.strip()  # 当前非 ANSI 端口行文本

            # 空行和整行注释都不属于裸端口名片段。
            if not str_stripped or str_stripped.startswith("//"):

                # 当前物理行不承载裸端口名信息。
                continue

            # 去掉行尾注释后，只保留端口名候选文本。
            str_raw, _ = self._split_comment(str_stripped)  # 当前非 ANSI 端口片段代码文本

            # 把去空白后的端口片段加入收集列表。
            list_fragments.append(str_raw.strip())

        # 没有任何有效端口片段时，说明不是非 ANSI 端口头。
        if not list_fragments:

            # 无片段输入时返回空列表，交由上层决定后续路径。
            return []

        # 记录按顺序提取出来的裸端口名。
        list_names: list[str] = []  # 非 ANSI 端口名结果列表

        # 顶层逗号切分后，逐个验证每个端口名候选。
        for str_entry in self._split_top_level("\n".join(list_fragments), ","):

            # 去掉片段尾部逗号和分号，得到最终候选端口名。
            str_candidate = str_entry.strip().rstrip(",;")  # 当前裸端口名候选

            # 空候选不构成有效端口名。
            if not str_candidate:

                # 仅由分隔符造成的空片段可以直接跳过。
                continue

            # 只有合法标识符才允许作为非 ANSI 端口名。
            if not re.fullmatch(r"[A-Za-z_]\w*", str_candidate):

                # 只要存在一个非标端口名，就放弃整条非 ANSI 回退路径。
                return []

            # 当前候选合法时，按原顺序并入端口名列表。
            list_names.append(str_candidate)

        # 返回解析得到的裸端口名列表。
        return list_names

    # 根据模块主体中的声明恢复非 ANSI 端口定义，并返回剩余正文。
    def _parse_non_ansi_ports_from_body(self, port_names: list[str], body: str) -> tuple[list[PortDecl], str]:
        """
        从模块主体中的 input/output/inout 声明恢复非 ANSI 端口信息。

        参数:
            port_names: 模块头中声明的裸端口名顺序。
            body: 模块主体原始文本。
        返回:
            tuple[list[PortDecl], str]: 按模块头顺序恢复出的端口列表，以及去掉已消费端口声明后的剩余主体。
        异常:
            VerilogFormatterError: 缺少任意非 ANSI 端口声明时抛出。
        """

        # 把主体拆成物理行，便于按前缀顺序消费声明。
        list_lines = body.splitlines()  # 模块主体物理行列表

        # 记录已经向前消费到的物理行下标。
        int_consumed = 0  # 当前已消费的主体行数

        # 用端口名映射缓存已恢复的非 ANSI 端口声明。
        dict_parsed: dict[str, PortDecl] = {}  # 已恢复的非 ANSI 端口声明映射

        # 收集未被当作非 ANSI 端口声明消费的剩余主体行。
        list_remaining_lines: list[str] = []  # 去掉非 ANSI 端口声明后的剩余主体行

        # 累积独立属性行，等待拼接到下一条真实端口声明。
        str_pending_attributes = ""  # 待拼接到下一条主体端口声明的属性文本

        # 从主体开头开始扫描，直到端口声明区结束。
        while int_consumed < len(list_lines):

            # 读取当前待消费物理行的规范化文本。
            str_stripped = list_lines[int_consumed].strip()  # 当前主体行的规范化文本

            # 空白行和纯注释行不属于可消费端口声明。
            if not str_stripped or str_stripped.startswith("//"):

                # 这类行仍需保留在剩余主体中。
                list_remaining_lines.append(list_lines[int_consumed])

                # 当前保留行已经写回剩余主体，继续消费下一行。
                int_consumed += 1  # 下一个待消费的主体行下标

                # 注释或空白行不参与非 ANSI 端口恢复。
                continue

            # 参数声明不属于非 ANSI 端口恢复范围，应整体保留。
            if str_stripped.startswith(("parameter", "localparam")):

                # 先回收完整参数声明，再整体写回剩余主体。
                tuple_declaration_result = self._collect_declaration_statement(list_lines, int_consumed)  # 当前参数声明的文本、注释和结束下标

                # 从声明回收结果里只取结束下标，供主体行推进复用。
                int_next_index = tuple_declaration_result[2]  # 参数声明结束后的下一物理行下标

                # 把完整参数声明原样保留回剩余主体中。
                list_remaining_lines.extend(list_lines[int_consumed:int_next_index])

                # 当前参数声明已经整体消费完成。
                int_consumed = int_next_index  # 参数声明之后的下一物理行下标

                # 参数声明不参与非 ANSI 端口恢复。
                continue

            # 独立属性行要缓存起来，等待下一条真实端口声明拼接。
            if str_stripped.startswith("(*"):

                # 先拆出属性行里的纯代码部分，方便后续识别真实 attributes。
                str_raw, _ = self._split_comment(str_stripped)  # 当前属性行代码部分

                # 再从属性行代码里拆出 attributes 以及可能混排的正文。
                str_attributes, str_candidate = self._extract_leading_attributes(str_raw)  # 属性行提取结果

                # 只有属性没有正文时，继续缓存等待后续端口声明。
                if str_attributes and not str_candidate:

                    # 累积当前属性文本，供下一条真实端口声明复用。
                    str_pending_attributes = f"{str_pending_attributes} {str_attributes}".strip()  # 累积到下一条主体端口声明前的属性文本

                    # 当前属性行已经消费完成，继续读取下一条主体行。
                    int_consumed += 1  # 属性行之后的下一物理行下标

                    # 只有属性没有正文时，当前行不构成真实端口声明。
                    continue

            # 一旦离开 input/output/inout 声明区，就结束非 ANSI 端口恢复。
            if not str_stripped.startswith(("input", "output", "inout")):

                # 当前行已经进入模块主体的普通声明或语句区。
                break

            # 拆出当前端口声明行的代码部分和行尾注释。
            str_raw, str_inline_comment = self._split_comment(str_stripped)  # 当前主体端口声明文本与注释

            # 若前面缓存过独立属性行，需要先拼回当前端口声明。
            if str_pending_attributes:

                # 还原属性前缀，让端口解析器看到完整声明。
                str_raw = f"{str_pending_attributes} {str_raw}".strip()  # 拼回属性后的完整主体端口声明

                # 走到这里说明属性前缀已经顺利拼回当前主体端口声明本体。
                str_pending_attributes = ""  # 当前主体端口声明已消费的属性缓存

            # 现在可以把主体里的这条声明按非 ANSI 端口规则还原。
            port_decl_node = self._parse_port_line(  # 当前非 ANSI 端口解析结果
                str_raw, str_inline_comment, "", "", ""  # 非 ANSI 端口文本、注释和空分组上下文
            )

            # 无法还原为单条端口声明时，必须要求先规范输入。
            if not port_decl_node:

                # 抛出非 ANSI 端口声明形态不稳定的统一异常。
                raise VerilogFormatterError(
                    "> ERR: [Python] non-ANSI port normalization failed."
                ) from self._strict_error(
                    "unsupported_shape",
                    str_stripped,
                    "Rewrite each port declaration into a single input/output/inout "
                    "form before formatting.",
                )

            # 用端口名缓存当前恢复出的非 ANSI 端口声明。
            dict_parsed[port_decl_node.name] = port_decl_node  # 当前端口名对应的恢复结果

            # 当前端口声明已经消费，继续向下一物理行推进。
            int_consumed += 1  # 下一条待消费的主体行下标

            # 模块头中的全部裸端口名都已恢复后，可以停止扫描端口区。
            if all(name in dict_parsed for name in port_names):

                # 非 ANSI 端口恢复目标已经全部达成。
                break

        # 统计模块头中仍未在主体里找到声明的端口名。
        list_missing = [name for name in port_names if name not in dict_parsed]  # 缺失的非 ANSI 端口名列表

        # 只要仍有端口名缺失，就不能继续保守格式化。
        if list_missing:

            # 抛出非 ANSI 端口声明缺失的统一异常。
            raise VerilogFormatterError(
                "> ERR: [Python] non-ANSI port declaration lookup failed."
            ) from self._strict_error(
                "unsupported_shape",
                ", ".join(list_missing),
                "Provide matching input/output/inout declarations for each non-ANSI "
                "module port before formatting.",
            )

        # 按模块头中的端口顺序重建最终端口列表。
        list_ordered_ports = [dict_parsed[name] for name in port_names]  # 按模块头顺序排列的端口列表

        # 把尚未消费的主体剩余部分重新接回结果中。
        list_remaining_lines.extend(list_lines[int_consumed:])

        # 返回恢复出的端口列表以及去掉端口声明后的剩余主体。
        return list_ordered_ports, "\n".join(list_remaining_lines)
