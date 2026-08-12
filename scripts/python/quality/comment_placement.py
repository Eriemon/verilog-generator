"""校验生成 Verilog 中注释位置和注释语义是否满足 skill 约束。"""

# 延迟注解求值，避免运行期导入 Path 与 Any 时触发额外类型解析。
from __future__ import annotations

# 正则用于识别 Verilog 构造和低质量注释模板。
import re

# dataclass 用于携带单文件扫描共享上下文，避免 helper 参数过长。
from dataclasses import dataclass

# Path 负责递归扫描生成的 Verilog 文件。
from pathlib import Path

# Any 只用于 JSON 风格报告字典的值类型表达。
from typing import Any

# 低信息注释模板用于阻断占位式或套话式 Verilog 注释。
GENERIC_COMMENT_PATTERNS = (  # Verilog 注释负例正则集合
    re.compile(r"泛泛"),  # 中文泛化注释标记
    re.compile(r"逐行中文注释"),  # 批量注释占位话术
    re.compile(r"占位"),  # 未替换占位文本
    re.compile(r"\bplaceholder\b", re.IGNORECASE),  # 英文占位文本
    re.compile(r"这里处理逻辑"),  # 无构造语义的模板注释
    re.compile(r"模块结束\s*$"),  # 未命名关闭对象的 endmodule 注释
    re.compile(r"//"),  # 注释正文中残留注释符号
)

# 单文件扫描上下文集中携带发现项、统计和语言策略。
@dataclass
class CommentPlacementContext:
    """保存 comment placement 单文件扫描期间共享的报告容器。"""

    # list_issues 是跨文件共享的 COMMENT_PLACEMENT 发现项列表。
    list_issues: list[dict[str, Any]]  # 注释位置违规发现项

    # dict_metrics 是跨文件共享的扫描统计字典。
    dict_metrics: dict[str, Any]  # 注释位置扫描统计

    # str_rel_path 写入发现项 path 字段。
    str_rel_path: str  # 当前 Verilog 文件相对扫描根路径

    # str_comment_language 控制中文注释门槛。
    str_comment_language: str  # 本轮期望注释语言

# 单行扫描上下文集中保存分类和注释正文。
@dataclass
class VerilogLineContext:
    """保存当前 Verilog 行的解析信息和构造分类。"""

    # list_infos 允许 helper 查询相邻行和原始行号。
    list_infos: list[dict[str, Any]]  # 当前文件完整逐行记录

    # int_index 是当前行在 list_infos 中的位置。
    int_index: int  # 当前扫描行索引

    # dict_info 是当前行的代码/注释记录。
    dict_info: dict[str, Any]  # 当前 Verilog 行记录

    # str_construct 是当前行的原始构造分类。
    str_construct: str  # 当前行 Verilog 构造类型

    # str_counted_construct 是写入指标统计的折叠分类。
    str_counted_construct: str  # 指标统计使用的构造类型

    # code_text 是去掉注释后的当前行 Verilog 代码。
    code_text: str  # 当前行 Verilog 代码文本

    # comment_text 是当前行提取出的注释正文。
    comment_text: str  # 当前行 Verilog 注释正文

# 对外入口扫描 Verilog 文件并累计注释位置问题。
def validate_comment_placement(root: Path, comment_language: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """
    检查目录下 Verilog 文件的同/邻行注释约束。

    :param root: 需要递归扫描的生成 RTL 根目录。
    :param comment_language: 期望注释语言；`zh` 要求注释正文包含中文。
    :return: 发现项列表和扫描统计指标。
    """

    # list_issues 保存 COMMENT_PLACEMENT 错误，供静态 lint 汇总。
    list_issues: list[dict[str, Any]] = []  # lint 调用方接收的 COMMENT_PLACEMENT 错误列表

    # dict_metrics 记录扫描规模和按构造分类的违规数量。
    dict_metrics: dict[str, Any] = {  # 注释位置扫描统计容器
        "scanned_files": 0,  # 已扫描 Verilog 文件数量
        "code_lines": 0,  # 含 Verilog 代码的行数
        "same_line_comment_lines": 0,  # 满足同/邻行注释要求的行数
        "violations": 0,  # 注释位置违规总数
        "by_construct": {},  # 构造类型到 checked/violations 的映射
    }

    # 按路径排序保证报告顺序稳定。
    for path_verilog in sorted(root.glob("**/*.v")):

        # 单文件扫描会更新 list_issues 和 dict_metrics。
        _scan_verilog_file(path_verilog, root, comment_language, list_issues, dict_metrics)

    # 返回发现项和统计信息给静态 lint 调用方。
    return list_issues, dict_metrics

# 单文件扫描封装宏续行状态和逐行分类逻辑。
def _scan_verilog_file(
    path_verilog: Path,
    root: Path,
    comment_language: str,
    list_issues: list[dict[str, Any]],
    dict_metrics: dict[str, Any],
) -> None:
    """
    扫描单个 Verilog 文件并把违规写入共享报告。

    :param path_verilog: 当前正在检查的 `.v` 文件。
    :param root: 扫描根目录，用于生成相对路径。
    :param comment_language: 期望注释语言。
    :param list_issues: 跨文件累计的发现项列表。
    :param dict_metrics: 跨文件累计的统计字典。
    :return: 无业务返回值，直接更新 `list_issues` 和 `dict_metrics`。
    """

    # rel_path 写入 lint 发现项，保持历史 path:line 格式。
    rel_path = path_verilog.relative_to(root).as_posix()  # 当前文件相对扫描根的 POSIX 路径

    # context 聚合单文件扫描中反复传递的共享报告状态。
    context = CommentPlacementContext(list_issues, dict_metrics, rel_path, comment_language)  # 单文件扫描上下文

    # testbench 文件允许 task/function helper，其余 RTL 禁止。
    bool_is_testbench = _is_testbench(path_verilog)  # 当前文件是否按测试平台处理

    # line_infos 把 Verilog 代码和注释切分成统一记录。
    list_line_infos = _verilog_line_infos(  # 当前 Verilog 文件的逐行解析记录
        path_verilog.read_text(encoding="utf-8", errors="ignore").splitlines()  # 忽略非法编码后的源码行
    )  # 当前文件的逐行代码/注释信息

    # 记录本文件已进入扫描。
    dict_metrics["scanned_files"] += 1  # 扫描文件累计数量

    # 宏续行状态用于禁止续行尾部行内注释。
    bool_in_multiline_macro = False  # 是否位于反斜杠延续的 define 宏体中

    # 逐行检查 Verilog 构造和注释位置。
    for int_index, dict_info in enumerate(list_line_infos):

        # 空代码行只作为邻接注释上下文，不参与构造计数。
        if not dict_info["has_code"]:

            # 跳过纯注释或空白行。
            continue

        # verilog_line_context_current 聚合当前行后续 helper 需要的全部解析信息。
        verilog_line_context_current = _build_line_context(  # 当前行扫描上下文
            list_line_infos,  # 相邻行检查需要的整文件记录
            int_index,  # 当前行在整文件记录中的索引
            dict_info,  # 当前行代码和注释记录
            bool_is_testbench,  # 当前文件是否使用 testbench 规则
        )

        # 当前行进入代码行统计。
        dict_metrics["code_lines"] += 1  # Verilog 代码行累计数量

        # checked 记录该构造被检查过一次。
        _bump(dict_metrics, verilog_line_context_current.str_counted_construct, "checked")

        # 宏续行需要在普通构造规则前优先处理。
        tuple_macro_state = _handle_multiline_macro_line(  # 宏规则的新状态与处理标记
            verilog_line_context_current,  # 泛化和构造专属检查所需行上下文
            bool_in_multiline_macro,  # 进入当前行前的宏续行状态
            context,  # 写入发现项和统计的扫描上下文
        )

        # 保存宏规则返回的新续行状态。
        bool_in_multiline_macro = tuple_macro_state[0]  # 处理当前行后的宏续行状态

        # 当前行若已由宏规则处理，则跳过普通构造检查。
        bool_macro_line_handled = tuple_macro_state[1]  # 当前行是否属于多行宏规则范围

        # 宏续行处理函数返回 True 且本行属于宏体时，普通规则不再重复检查。
        if bool_macro_line_handled:

            # 续行宏规则已经完成本行诊断。
            continue

        # 不要求同/邻行注释的构造只检查已有注释的质量。
        if not _construct_requires_same_line_comment(
            verilog_line_context_current.str_construct,
            verilog_line_context_current.code_text,
        ):

            # 非强制注释构造仍可统计有效同/邻行注释。
            _handle_optional_comment_line(
                context,
                verilog_line_context_current.str_counted_construct,
                dict_info,
                verilog_line_context_current.str_construct,
                verilog_line_context_current.comment_text,
            )

            # 进入下一条 Verilog 代码行。
            continue

        # 强制注释构造需要先确认注释是否存在并满足语言要求。
        if _required_comment_is_available(
            verilog_line_context_current.list_infos,
            verilog_line_context_current.int_index,
            verilog_line_context_current.str_construct,
            verilog_line_context_current.comment_text,
            comment_language,
        ):

            # 当前构造已有合格注释证据。
            dict_metrics["same_line_comment_lines"] += 1  # 满足强制注释要求的行数

        # 同/邻行注释缺失时登记强制注释违规
        else:

            # 缺失注释时登记当前构造违规。
            _add_issue(
                context.list_issues,
                context.dict_metrics,
                verilog_line_context_current.str_counted_construct,
                "Verilog code line must use a same-line or adjacent explanatory comment in the requested language.",
                context.str_rel_path,
                int(dict_info["line_no"]),
            )

            # 缺注释后无需继续检查注释质量。
            continue

        # 注释存在后再检查泛化、end 命名、generate label 和 task/function 边界。
        _validate_construct_comment_quality(
            verilog_line_context_current,  # 构造质量检查需要的行上下文
            bool_is_testbench,  # 当前文件是否测试平台
            context,  # 发现项写入使用的扫描上下文
        )

# 单行上下文构造集中处理代码、注释和构造分类。
def _build_line_context(
    list_line_infos: list[dict[str, Any]],
    int_index: int,
    dict_info: dict[str, Any],
    bool_is_testbench: bool,
) -> VerilogLineContext:
    """
    根据当前行记录构造 VerilogLineContext。

    :param list_line_infos: 当前文件完整逐行记录。
    :param int_index: 当前行在逐行记录中的索引。
    :param dict_info: 当前行代码/注释记录。
    :param bool_is_testbench: 当前文件是否按 testbench 规则处理。
    :return: 带代码、注释和构造分类的行上下文。
    """

    # code_text 是去掉 Verilog 注释后的源码片段。
    code_text = str(dict_info["code"]).strip()  # 用于构造分类的代码片段

    # comment_text 是当前行 Verilog 注释正文。
    comment_text = str(dict_info["comment"]).strip()  # 用于语言和泛化检查的注释片段

    # str_construct 决定该行是否必须带同/邻行注释。
    str_construct = _classify_construct(code_text, bool_is_testbench)  # 注释策略分派构造类型

    # str_counted_construct 折叠 end 构造，保证指标分类稳定。
    str_counted_construct = _metric_construct(str_construct)  # 指标统计使用的构造分类

    # 返回当前行后续 helper 共享的解析上下文。
    return VerilogLineContext(
        # 当前文件和当前行定位。
        list_line_infos,
        int_index,
        dict_info,

        # 构造分类同时服务规则分派和统计桶。
        str_construct,
        str_counted_construct,

        # 代码片段和注释片段服务后续质量检查。
        code_text,
        comment_text,
    )

# 宏续行规则优先于普通同/邻行注释规则。
def _handle_multiline_macro_line(
    line_context: VerilogLineContext,
    in_multiline_macro: bool,
    context: CommentPlacementContext,
) -> tuple[bool, bool]:
    """
    处理反斜杠延续的多行 `define` 注释约束。

    :param line_context: 当前 Verilog 行的解析信息。
    :param in_multiline_macro: 进入本行前是否处于宏续行体。
    :param context: 单文件扫描共享上下文。
    :return: 处理当前行后的宏续行状态，以及当前行是否已由宏规则处理。
    """

    # 已在宏体中时，禁止续行尾部携带注释。
    if in_multiline_macro:

        # 宏体续行存在注释会影响宏文本拼接。
        if line_context.comment_text:

            # 续行注释违规写入 lint 发现项。
            _add_issue(
                context.list_issues,
                context.dict_metrics,
                line_context.str_counted_construct,
                (
                    "Multiline macro continuation must not carry an inline comment; "
                    "bind one pure comment before the macro."
                ),
                context.str_rel_path,
                int(line_context.dict_info["line_no"]),
            )

        # 反斜杠消失代表宏续行结束。
        if not line_context.code_text.endswith("\\"):

            # 当前行关闭多行宏状态。
            return False, True

        # 仍在宏体中，下一行继续走宏续行规则。
        return True, True

    # 新的多行 define 需要纯前导注释说明宏用途。
    if line_context.code_text.startswith("`define") and line_context.code_text.endswith("\\"):

        # `define 起始行自身不能携带行内注释。
        if line_context.comment_text:

            # 起始行注释违规会破坏宏延续可读性。
            _add_issue(
                context.list_issues,
                context.dict_metrics,
                line_context.str_counted_construct,
                "Multiline macro `define line must use a pure leading comment, not an inline continuation comment.",
                context.str_rel_path,
                int(line_context.dict_info["line_no"]),
            )

        # 多行宏必须由前一行纯注释描述宏语义。
        if not _valid_leading_comment(
            line_context.list_infos,
            line_context.int_index,
            context.str_comment_language,
            keyword="宏",
        ):

            # 缺少前导说明时登记宏级违规。
            _add_issue(
                context.list_issues,
                context.dict_metrics,
                line_context.str_counted_construct,
                "Multiline macro must have a pure explanatory comment immediately before the `define line.",
                context.str_rel_path,
                int(line_context.dict_info["line_no"]),
            )

        # 后续行进入宏续行规则。
        return True, True

    # 普通行不改变宏续行状态。
    return False, False

# 非强制注释构造只在注释存在时检查质量。
def _handle_optional_comment_line(
    context: CommentPlacementContext,
    str_counted_construct: str,
    info: dict[str, Any],
    str_construct: str,
    comment: str,
) -> None:
    """
    处理 module/end/case 等非强制同/邻行注释构造。

    :param context: 单文件扫描共享上下文。
    :param str_counted_construct: 指标统计使用的构造分类。
    :param info: 当前行代码/注释记录。
    :param str_construct: 当前行 Verilog 构造分类。
    :param comment: 当前行注释正文。
    :return: 无业务返回值，直接更新报告容器。
    """

    # 没有合格注释时，非强制构造不登记违规。
    if not comment or not _comment_satisfies_language(comment, context.str_comment_language):

        # 可选注释缺失不影响当前构造。
        return

    # 当前行已有可统计的同/邻行注释。
    context.dict_metrics["same_line_comment_lines"] += 1  # 可选构造上的有效注释行数

    # end 类构造存在注释时必须说明关闭对象。
    if str_construct in {"module_end", "task_end", "function_end", "generate_end"} and not _valid_end_comment(comment):

        # end 注释格式不合格时登记违规。
        _add_issue(
            context.list_issues,
            context.dict_metrics,
            str_counted_construct,
            "End construct comment must name the construct being closed and start with an end/结束 phrase.",
            context.str_rel_path,
            int(info["line_no"]),
        )

# 强制注释构造允许 always 使用邻接纯注释。
def _required_comment_is_available(
    infos: list[dict[str, Any]],
    index: int,
    str_construct: str,
    comment: str,
    comment_language: str,
) -> bool:
    """
    判断强制注释构造是否已经具备合格说明。

    :param infos: 当前文件的逐行代码/注释记录。
    :param index: 当前行在 `infos` 中的位置。
    :param str_construct: 当前行 Verilog 构造分类。
    :param comment: 当前行注释正文。
    :param comment_language: 期望注释语言。
    :return: True 表示同/邻行注释满足语言要求。
    """

    # 同行注释满足语言要求时直接通过。
    if _comment_satisfies_language(comment, comment_language):

        # 同行注释是首选证据。
        return True

    # always/initial 允许使用相邻纯注释说明过程块目的。
    if str_construct == "always" and _valid_adjacent_comment(infos, index, comment_language):

        # 邻接纯注释可替代 always 行内注释。
        return True

    # 其他强制注释构造必须在同一行给出说明。
    return False

# 同/邻行注释存在后继续检查构造专属质量。
def _validate_construct_comment_quality(
    line_context: VerilogLineContext,
    is_testbench: bool,
    context: CommentPlacementContext,
) -> None:
    """
    检查泛化注释、结束注释、generate 标签和 task/function 约束。

    :param line_context: 当前 Verilog 行的解析信息。
    :param is_testbench: 当前文件是否为测试平台。
    :param context: 单文件扫描共享上下文。
    :return: 无业务返回值，直接更新报告容器。
    """

    # counted_construct 统一折叠 end/task/function 构造到指标分类。
    str_counted_construct = line_context.str_counted_construct  # 当前违规写入的统计分类

    # 泛化注释无法证明生成器理解了当前 Verilog 构造。
    if _comment_is_generic(line_context.comment_text):

        # 泛化注释登记为当前构造违规。
        _add_issue(
            context.list_issues,
            context.dict_metrics,
            str_counted_construct,
            (
                "Generic Verilog comment is not allowed; describe the construct, "
                "signal, condition, or verification purpose."
            ),
            context.str_rel_path,
            int(line_context.dict_info["line_no"]),
        )

    # end 类构造的注释必须说明关闭对象。
    if (
        line_context.str_construct in {"module_end", "task_end", "function_end", "generate_end"}
        and not _valid_end_comment(line_context.comment_text)
    ):

        # end 注释缺少结束短语时登记违规。
        _add_issue(
            context.list_issues,
            context.dict_metrics,
            str_counted_construct,
            "End construct comment must name the construct being closed and start with an end/结束 phrase.",
            context.str_rel_path,
            int(line_context.dict_info["line_no"]),
        )

    # module 声明注释必须说明模块或测试平台用途。
    if line_context.str_construct == "module" and not _module_comment_valid(line_context.comment_text):

        # module 注释缺少模块语义时登记违规。
        _add_issue(
            context.list_issues,
            context.dict_metrics,
            str_counted_construct,
            "Module declaration comment must identify the module or testbench purpose.",
            context.str_rel_path,
            int(line_context.dict_info["line_no"]),
        )

    # 命名 generate 分支必须使用 gen_ 前缀。
    bool_generate_label_missing_prefix = (  # generate 具名分支是否缺少前缀
        line_context.str_construct == "generate"  # 当前行已经被分类为 generate
        and "begin:" in line_context.code_text  # 只有具名 begin 分支需要标签规则
        and "gen_" not in line_context.code_text  # 标签中未出现 gen_ 前缀
    )  # generate 具名分支缺少 gen_ 前缀

    # 命名 generate 分支缺少 gen_ 前缀时登记违规。
    if bool_generate_label_missing_prefix:

        # generate 标签缺少 gen_ 前缀时登记违规。
        _add_issue(
            context.list_issues,
            context.dict_metrics,
            str_counted_construct,
            "Generate branch labels must begin with `gen_` and the same line must explain the branch.",
            context.str_rel_path,
            int(line_context.dict_info["line_no"]),
        )

    # task/function 只能出现在测试平台并带前导说明。
    if line_context.str_construct in {"task", "function"}:

        # task/function 的文件边界和前导注释由专用 helper 检查。
        _validate_task_or_function_comment(
            line_context.list_infos,
            line_context.int_index,
            is_testbench,
            context,
            str_counted_construct,
            line_context.str_construct,
        )

# 测试平台 task/function 需要纯前导注释，RTL 中则禁止。
def _validate_task_or_function_comment(
    infos: list[dict[str, Any]],
    index: int,
    is_testbench: bool,
    context: CommentPlacementContext,
    str_counted_construct: str,
    str_construct: str,
) -> None:
    """
    检查 task/function 是否只在 testbench 中以说明注释出现。

    :param infos: 当前文件的逐行代码/注释记录。
    :param index: 当前行在 `infos` 中的位置。
    :param is_testbench: 当前文件是否为测试平台。
    :param context: 单文件扫描共享上下文。
    :param str_counted_construct: 指标统计使用的构造分类。
    :param str_construct: 当前行 Verilog 构造分类。
    :return: 无业务返回值，直接更新报告容器。
    """

    # 非 testbench RTL 禁止 task/function helper。
    if not is_testbench:

        # RTL 中出现 task/function 时直接登记违规。
        _add_issue(
            context.list_issues,
            context.dict_metrics,
            str_counted_construct,
            "RTL task/function blocks are not allowed; only testbenches may use documented helpers.",
            context.str_rel_path,
            int(infos[index]["line_no"]),
        )

        # RTL 禁止路径不再检查前导注释。
        return

    # task/function 在 testbench 中必须有对应中文前导说明。
    str_keyword = "任务" if str_construct == "task" else "函数"  # 前导注释必须包含的中文关键词

    # 测试平台 helper 缺少前导说明时登记违规。
    if not _valid_leading_comment(infos, index, context.str_comment_language, keyword=str_keyword):

        # 前导注释缺失会降低 testbench helper 可读性。
        _add_issue(
            context.list_issues,
            context.dict_metrics,
            str_counted_construct,
            "Testbench task/function declarations must have a pure leading purpose comment.",
            context.str_rel_path,
            int(infos[index]["line_no"]),
        )

# 统一登记 COMMENT_PLACEMENT 发现项并同步统计。
def _add_issue(
    list_issues: list[dict[str, Any]],
    dict_metrics: dict[str, Any],
    str_construct: str,
    message: str,
    rel: str,
    line_no: int,
) -> None:
    """
    把单条注释位置违规写入发现项列表。

    :param list_issues: 共享发现项列表。
    :param dict_metrics: 共享统计字典。
    :param str_construct: 指标统计使用的构造分类。
    :param message: 面向调用者的英文违规说明。
    :param rel: 当前文件相对扫描根的路径。
    :param line_no: 当前 Verilog 文件中的 1 基行号。
    :return: 无业务返回值，直接更新 `list_issues` 和 `dict_metrics`。
    """

    # dict_issue 保持 static_lint 已消费的字段形状。
    dict_issue = {  # COMMENT_PLACEMENT 单条错误发现项
        "severity": "error",  # 注释位置问题均按 error 输出
        "message": message,  # 英文诊断说明
        "path": f"{rel}:{line_no}",  # 相对路径和 Verilog 行号
        "stage": "static",  # 发现项来自静态 lint 阶段
        "source": "current_module_issue",  # 与其他当前模块 lint 来源保持一致
        "detail": f"COMMENT_PLACEMENT construct={str_construct} line={line_no}",  # 机器可读细节
    }

    # 追加发现项到共享列表。
    list_issues.append(dict_issue)

    # 顶层违规数同步递增。
    dict_metrics["violations"] += 1  # 全部构造违规总量

    # 构造级违规数同步递增。
    _bump(dict_metrics, str_construct, "violations")

# 构造级 checked/violations 计数器递增。
def _bump(dict_metrics: dict[str, Any], str_construct: str, key: str) -> None:
    """
    更新单个 Verilog 构造分类的统计桶。

    :param dict_metrics: 共享统计字典。
    :param str_construct: 指标统计使用的构造分类。
    :param key: 需要递增的桶内字段，通常是 `checked` 或 `violations`。
    :return: 无业务返回值，直接更新 `dict_metrics`。
    """

    # by_construct 是构造名到计数桶的映射。
    dict_by_construct = dict_metrics.setdefault("by_construct", {})  # 构造维度统计映射

    # 新构造首次出现时建立 checked/violations 双计数。
    dict_bucket = dict_by_construct.setdefault(str_construct, {"checked": 0, "violations": 0})  # 当前构造计数桶

    # 指定计数字段递增一次。
    dict_bucket[key] = int(dict_bucket.get(key, 0)) + 1  # 当前构造指定计数值

# Verilog 单行代码分类，控制后续注释策略。
def _classify_construct(code: str, is_tb: bool) -> str:
    """
    根据一行 Verilog 代码推断构造类型。

    :param code: 已移除注释的 Verilog 代码文本。
    :param is_tb: 当前文件是否为测试平台；保留参数以兼容旧签名。
    :return: 构造分类名称。
    """

    # is_tb 当前不参与分类，保留以避免调用方签名漂移。
    del is_tb

    # stripped 是分类正则使用的规范化代码文本。
    stripped = code.strip()  # 去除首尾空白后的 Verilog 代码

    # end_result 优先识别各类结束构造。
    str_end_result = _classify_end_construct(stripped)  # 结束关键字对应的关闭构造分类

    # 命中结束构造时直接返回。
    if str_end_result:

        # 返回结束构造分类。
        return str_end_result

    # declaration_result 覆盖 module、宏、端口、参数和信号声明。
    str_declaration_result = _classify_declaration_construct(stripped)  # 声明类 Verilog 构造分类

    # 命中声明构造时直接返回。
    if str_declaration_result:

        # 返回声明构造分类。
        return str_declaration_result

    # 过程块分类覆盖 task/function/generate/always/case 的首次命中。
    str_behavior_result = _classify_behavior_construct(stripped)  # 过程块或测试平台 helper 分类

    # 命中行为构造时直接返回。
    if str_behavior_result:

        # 返回行为构造分类。
        return str_behavior_result

    # branch_or_instance_result 覆盖条件分支和模块实例化。
    str_branch_or_instance_result = _classify_branch_or_instance(stripped)  # 分支或实例化分类

    # 命中分支或实例化时直接返回。
    if str_branch_or_instance_result:

        # 返回分支或实例化分类。
        return str_branch_or_instance_result

    # 未命中特定模式时按普通语句处理。
    return "statement"

# end 类 Verilog 构造优先识别，避免被普通语句吞掉。
def _classify_end_construct(stripped: str) -> str | None:
    """
    识别 endmodule、endtask、endfunction 和 endgenerate。

    :param stripped: 去除首尾空白后的 Verilog 代码。
    :return: 对应结束构造分类；无法识别时返回 None。
    """

    # endmodule 独立归入 module_end，便于注释关闭语义检查。
    if stripped.startswith("endmodule"):

        # 返回模块结束构造。
        return "module_end"

    # endtask 归入测试平台任务结束。
    if stripped.startswith("endtask"):

        # 返回任务关闭构造。
        return "task_end"

    # endfunction 归入测试平台函数结束。
    if stripped.startswith("endfunction"):

        # 返回函数关闭构造。
        return "function_end"

    # endgenerate 参与 generate 注释统计。
    if stripped.startswith("endgenerate"):

        # 返回生成块关闭构造。
        return "generate_end"

    # 当前行不是结束构造。
    return None

# 声明类 Verilog 构造需要检查同一行用途注释。
def _classify_declaration_construct(stripped: str) -> str | None:
    """
    识别 module、宏、预处理指令、参数、端口、信号和 assign。

    :param stripped: 去除首尾空白后的 Verilog 代码。
    :return: 声明类构造分类；无法识别时返回 None。
    """

    # module 声明行需要验证模块用途注释。
    if re.match(r"^module\b", stripped):

        # 返回模块声明构造。
        return "module"

    # `define 宏行需要专门处理多行延续。
    if stripped.startswith("`define"):

        # 返回宏定义构造。
        return "macro"

    # 其他反引号行按预处理指令处理。
    if stripped.startswith("`"):

        # 返回预处理指令构造。
        return "directive"

    # 参数声明必须携带同一行语义注释。
    if re.match(r"^(parameter|localparam)\b", stripped):

        # 返回参数声明构造。
        return "parameter"

    # 端口声明需要解释信号方向和用途。
    if re.match(r"^(input|output|inout)\b", stripped):

        # 返回端口声明构造。
        return "port"

    # 常见信号声明需要解释寄存器、连线或循环变量用途。
    if re.match(r"^(reg|wire|integer|genvar)\b", stripped):

        # 返回信号声明构造。
        return "signal"

    # assign 连续赋值需要说明组合逻辑含义。
    if re.match(r"^assign\b", stripped):

        # 返回连续赋值构造。
        return "assign"

    # 当前行不是声明类构造。
    return None

# 行为块和过程块分类覆盖 task/function/generate/always/case。
def _classify_behavior_construct(stripped: str) -> str | None:
    """
    识别 task、function、generate、always/initial 和 case。

    :param stripped: 去除首尾空白后的 Verilog 代码。
    :return: 行为类构造分类；无法识别时返回 None。
    """

    # task 声明只允许出现在测试平台。
    if re.match(r"^(task)\b", stripped):

        # 返回任务 helper 构造。
        return "task"

    # function 声明在 RTL 中禁用，测试平台路径另行检查。
    if re.match(r"^(function)\b", stripped):

        # 返回函数 helper 构造。
        return "function"

    # generate 或具名 gen_ begin 分支按 generate 处理。
    if stripped.startswith("generate") or ("begin:" in stripped and "gen_" in stripped):

        # 返回生成块构造。
        return "generate"

    # always/initial 过程块允许相邻纯注释。
    if re.match(r"^(always|initial)\b", stripped):

        # 返回过程块构造。
        return "always"

    # case/endcase 分支语义通常由内部 item 注释承载。
    if re.match(r"^(case|endcase)\b", stripped):

        # 返回选择分支构造。
        return "case"

    # 当前行不是行为块构造。
    return None

# 条件分支和模块实例化是分类链最后一层。
def _classify_branch_or_instance(stripped: str) -> str | None:
    """
    识别条件分支、case item 和模块实例化。

    :param stripped: 去除首尾空白后的 Verilog 代码。
    :return: `branch`、`instance` 或 None。
    """

    # 条件分支和全大写 case item 视为 branch。
    if re.match(r"^(if|else|default)\b", stripped) or re.match(r"^[A-Z][A-Z0-9_]*\s*:", stripped):

        # 返回条件或 case item 分支构造。
        return "branch"

    # 模块实例化需要解释连接对象或功能。
    if _looks_like_instance(stripped):

        # 返回模块实例化构造。
        return "instance"

    # 当前行既不是分支也不是实例化。
    return None

# 指定构造是否强制要求同/邻行注释。
def _construct_requires_same_line_comment(str_construct: str, code: str) -> bool:
    """
    判断 Verilog 构造是否必须携带解释性注释。

    :param str_construct: 当前行 Verilog 构造分类。
    :param code: 当前行 Verilog 代码文本。
    :return: True 表示该构造需要同/邻行注释。
    """

    # stripped 用于识别纯结构符号行。
    stripped = code.strip()  # 去空白后的 Verilog 代码

    # 这些构造必须在生成代码中解释具体作用。
    if str_construct in {"parameter", "port", "signal", "assign", "instance", "task", "function", "generate"}:

        # 声明、赋值、实例和 helper 声明均需要解释。
        return True

    # always/initial 可用同一行或邻接纯注释解释。
    if str_construct == "always":

        # 过程块需要说明触发条件或行为目的。
        return True

    # 模块、指令、分支和普通语句默认不强制同一行说明。
    if str_construct in {"directive", "module", "module_end", "case", "branch", "statement"}:

        # 可选注释构造不强制报缺失。
        return False

    # 纯括号和 begin/end 行不承载独立语义。
    if stripped in {"#(", "(", ")", ");", "begin", "end"}:

        # 结构符号行不要求独立注释。
        return False

    # 其他未知构造保持宽松，避免误伤合法 Verilog。
    return False

# always/initial 行可以使用相邻纯注释说明块目的。
def _valid_adjacent_comment(infos: list[dict[str, Any]], index: int, comment_language: str) -> bool:
    """
    判断当前行上下相邻行是否存在合格纯注释。

    :param infos: 当前文件的逐行代码/注释记录。
    :param index: 当前行在 `infos` 中的位置。
    :param comment_language: 期望注释语言。
    :return: True 表示邻接纯注释满足语言和非泛化要求。
    """

    # 只检查前一行和后一行，保持旧规则的局部邻接边界。
    for int_neighbor in (index - 1, index + 1):

        # 邻接索引必须落在文件范围内。
        if 0 <= int_neighbor < len(infos):

            # 邻接行记录提供 pure_comment 和 comment 字段。
            dict_info = infos[int_neighbor]  # 相邻行代码/注释记录

            # comment_text 是相邻纯注释正文。
            comment_text = str(dict_info.get("comment") or "").strip()  # 相邻行注释正文

            # 纯注释、语言匹配且非泛化时可作为 always 说明。
            if (
                dict_info.get("pure_comment")
                and _comment_satisfies_language(comment_text, comment_language)
                and not _comment_is_generic(comment_text)
            ):

                # 找到有效邻接注释。
                return True

    # 两侧都没有合格纯注释。
    return False

# 指标分类折叠 end 构造。
def _metric_construct(str_construct: str) -> str:
    """
    把具体构造映射到统计报告使用的聚合分类。

    :param str_construct: 当前行 Verilog 构造分类。
    :return: 指标报告中的构造分类。
    """

    # module_end 统计到 module，避免单独产生结束分类。
    if str_construct in {"module_end"}:

        # 返回模块聚合分类。
        return "module"

    # task/function 的开始和结束都归入 testbench_task。
    if str_construct in {"task", "task_end", "function", "function_end"}:

        # 返回测试平台 helper 聚合分类。
        return "testbench_task"

    # generate_end 统计到 generate。
    if str_construct in {"generate_end"}:

        # 返回 generate 聚合分类。
        return "generate"

    # 其他构造保持原分类。
    return str_construct

# 简单实例化识别用于要求实例行解释。
def _looks_like_instance(code: str) -> bool:
    """
    粗略判断 Verilog 代码行是否像模块实例化。

    :param code: 去空白后的 Verilog 代码。
    :return: True 表示该行符合常见实例化形态。
    """

    # 控制语句和 assign/always 等关键字不能误判为实例。
    if re.match(r"^(if|for|case|assign|always|initial|else|begin|end)\b", code):

        # 关键字行不是模块实例化。
        return False

    # 普通实例和参数化实例两类形态都需要识别。
    return bool(
        re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", code)
        or re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*#\s*\(", code)
    )

# 前导纯注释用于说明多行宏和 testbench helper。
def _valid_leading_comment(infos: list[dict[str, Any]], index: int, comment_language: str, *, keyword: str) -> bool:
    """
    检查当前行上一行是否为合格前导纯注释。

    :param infos: 当前文件的逐行代码/注释记录。
    :param index: 当前行在 `infos` 中的位置。
    :param comment_language: 期望注释语言。
    :param keyword: 中文场景下要求出现的关键词。
    :return: True 表示上一行纯注释可解释当前构造。
    """

    # previous 指向当前行的上一行。
    int_previous = index - 1  # 前导注释候选行索引

    # 文件第一行没有前导注释。
    if int_previous < 0:

        # 不存在前导行。
        return False

    # info 保存前导行代码/注释记录。
    dict_info = infos[int_previous]  # 前导行记录

    # comment_text 是前导行注释正文。
    comment_text = str(dict_info["comment"]).strip()  # 前导注释正文

    # 前导行必须是纯注释且满足语言要求。
    if not dict_info["pure_comment"] or not _comment_satisfies_language(comment_text, comment_language):

        # 非纯注释不能解释下方构造。
        return False

    # 泛化前导注释不能作为合格说明。
    if _comment_is_generic(comment_text):

        # 占位或模板注释无效。
        return False

    # 中文模式要求注释显式提到目标构造关键词。
    return keyword in comment_text or comment_language != "zh"

# module 声明注释必须说明模块或 testbench 角色。
def _module_comment_valid(comment: str) -> bool:
    """
    判断 module 声明注释是否包含模块角色词。

    :param comment: module 声明行的注释正文。
    :return: True 表示注释点明模块或测试平台用途。
    """

    # module/testbench 关键词覆盖中英文注释。
    return any(str_token in comment for str_token in ("模块", "测试平台", "module", "testbench"))

# end 类构造注释必须以结束短语开头。
def _valid_end_comment(comment: str) -> bool:
    """
    判断 endmodule/endtask 等注释是否说明关闭语义。

    :param comment: end 构造行的注释正文。
    :return: True 表示注释以结束短语开头。
    """

    # stripped 是去空白后的注释正文。
    stripped = comment.strip()  # end 构造注释正文

    # 中英文结束短语均保持兼容。
    return stripped.startswith("结束") or stripped.lower().startswith(("end ", "end:"))

# 泛化注释识别使用固定负例正则。
def _comment_is_generic(comment: str) -> bool:
    """
    判断 Verilog 注释是否命中低信息模板。

    :param comment: 需要检查的 Verilog 注释正文。
    :return: True 表示注释属于占位或泛化文本。
    """

    # stripped 用于正则匹配前清理首尾空白。
    stripped = comment.strip()  # 待匹配的注释正文

    # 任一负例正则命中即判定为泛化注释。
    return any(pattern.search(stripped) for pattern in GENERIC_COMMENT_PATTERNS)

# 注释语言检查保持 zh 模式下至少包含一个中文字符。
def _comment_satisfies_language(comment: str, comment_language: str) -> bool:
    """
    判断注释正文是否满足请求语言。

    :param comment: 需要检查的 Verilog 注释正文。
    :param comment_language: 期望注释语言。
    :return: True 表示注释满足语言要求。
    """

    # normalized 去除首尾空白，避免空注释误判。
    normalized = comment.strip()  # 语言检查使用的注释正文

    # 空注释不满足任何语言要求。
    if not normalized:

        # 缺少注释正文。
        return False

    # 中文模式要求至少一个中日韩统一表意字符。
    if comment_language == "zh":

        # 任一中文字符即可满足语言门槛。
        return any("\u4e00" <= char <= "\u9fff" for char in normalized)

    # 非中文模式不限制具体字符集。
    return True

# Verilog 行切分入口保留 block comment 状态。
def _verilog_line_infos(lines: list[str]) -> list[dict[str, Any]]:
    """
    把 Verilog 源码行切分为代码、注释和纯注释标记。

    :param lines: 原始 Verilog 文件行。
    :return: 每行的 line_no/code/comment/pure_comment 记录。
    """

    # infos 按原始行号顺序保存切分结果。
    list_infos: list[dict[str, Any]] = []  # Verilog 逐行解析记录

    # block comment 状态会跨行延续。
    bool_in_block_comment = False  # 当前是否位于 /* */ 注释块内部

    # 逐行拆分代码与注释片段。
    for int_line_no, line_text in enumerate(lines, start=1):

        # split helper 返回当前行代码、注释和新的 block 状态。
        tuple_split_result = _split_verilog_code_and_comment(  # 单行 Verilog 切分三元组
            line_text,  # 原始 Verilog 行文本
            bool_in_block_comment,  # 进入当前行前的块注释状态
        )

        # code_text 保留当前行未被注释覆盖的 Verilog 片段。
        code_text = tuple_split_result[0]  # 当前行代码片段

        # comment_text 聚合当前行 // 或 /* */ 注释正文。
        comment_text = tuple_split_result[1]  # 当前行注释片段

        # bool_in_block_comment 更新跨行块注释状态。
        bool_in_block_comment = tuple_split_result[2]  # 离开当前行后的块注释状态

        # has_code 标记当前行是否仍含 Verilog 代码。
        bool_has_code = bool(code_text.strip())  # 当前行是否包含代码

        # has_comment 标记当前行是否含注释正文。
        bool_has_comment = bool(comment_text.strip())  # 当前行是否包含注释

        # 当前行记录保持旧字段名称。
        dict_info = {  # 单行 Verilog 代码/注释记录
            "line_no": int_line_no,  # 原始 Verilog 行号
            "code": code_text,  # 去除注释后的代码片段
            "has_code": bool_has_code,  # 当前行是否有代码
            "comment": comment_text.strip(),  # 行内或块注释的规范化正文
            "pure_comment": bool_has_comment and not bool_has_code,  # 当前行是否为纯注释
        }

        # 追加当前行解析记录。
        list_infos.append(dict_info)

    # 返回完整逐行记录。
    return list_infos

# 单行 Verilog 代码与注释切分，支持 // 和 /* */。
def _split_verilog_code_and_comment(line: str, bool_in_block_comment: bool) -> tuple[str, str, bool]:
    """
    切分一行 Verilog 的代码片段和注释片段。

    :param line: 原始 Verilog 源码行。
    :param bool_in_block_comment: 进入本行前是否处于块注释内部。
    :return: 代码文本、注释文本和离开本行后的块注释状态。
    """

    # code_parts 保留未被注释覆盖的字符。
    list_code_parts: list[str] = []  # 当前行代码字符片段

    # comment_parts 聚合行注释和块注释正文。
    list_comment_parts: list[str] = []  # 当前行注释正文片段

    # index 按字符推进，保留旧 parser 的简单状态机行为。
    int_index = 0  # 当前扫描字符位置

    # 持续扫描直到行尾或遇到 // 行注释。
    while int_index < len(line):

        # 已在块注释内部时只寻找结束符。
        if bool_in_block_comment:

            # end_index 定位当前行中的块注释结束符。
            int_end_index = line.find("*/", int_index)  # 块注释结束符位置

            # 当前行没有结束符时，整段剩余文本都是注释。
            if int_end_index == -1:

                # 收集跨行块注释正文。
                list_comment_parts.append(line[int_index:])

                # 返回仍在块注释中的状态。
                return "".join(list_code_parts), " ".join(list_comment_parts), True

            # 收集结束符之前的块注释正文。
            list_comment_parts.append(line[int_index:int_end_index])

            # 同行块注释结束后恢复代码扫描。
            int_index = int_end_index + 2  # 块注释结束后的扫描位置

            # 当前行已离开块注释。
            bool_in_block_comment = False  # 当前行块注释已经闭合

            # 继续扫描同一行的剩余字符。
            continue

        # 双斜线开启行注释，后续字符全部归入注释。
        if line.startswith("//", int_index):

            # 收集 // 后的注释正文。
            list_comment_parts.append(line[int_index + 2 :])

            # 行注释终止本行扫描。
            break

        # 斜星开启块注释，可能在同一行结束。
        if line.startswith("/*", int_index):

            # end_index 定位同一行内的块注释结束符。
            int_end_index = line.find("*/", int_index + 2)  # 同行块注释结束位置

            # 未找到结束符时进入跨行块注释。
            if int_end_index == -1:

                # 收集块注释起始后的剩余正文。
                list_comment_parts.append(line[int_index + 2 :])

                # 返回进入块注释的状态。
                return "".join(list_code_parts), " ".join(list_comment_parts), True

            # 同行块注释只收集中间正文。
            list_comment_parts.append(line[int_index + 2 : int_end_index])

            # 跳过 */ 后继续扫描后续代码。
            int_index = int_end_index + 2  # 同行块注释后的扫描位置

            # 继续扫描同一行后续字符。
            continue

        # 普通字符属于代码片段。
        list_code_parts.append(line[int_index])

        # 前进到下一个字符。
        int_index += 1  # 下一个字符索引

    # 返回当前行拆分结果和块注释状态。
    return "".join(list_code_parts), " ".join(list_comment_parts), False

# 测试平台文件名用于放宽 task/function 规则。
def _is_testbench(path: Path) -> bool:
    """
    根据文件名判断 Verilog 文件是否为测试平台。

    :param path: 当前 Verilog 文件路径。
    :return: True 表示文件名符合常见 testbench 命名。
    """

    # str_stem 用小写形式匹配 tb 前后缀和 testbench 词。
    str_stem = path.stem.lower()  # 小写文件主名

    # 常见 testbench 命名包括 *_tb、tb_* 和包含 testbench。
    return str_stem.endswith("_tb") or str_stem.startswith("tb_") or "testbench" in str_stem
