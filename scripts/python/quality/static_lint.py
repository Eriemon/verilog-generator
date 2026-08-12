"""对生成的 Verilog RTL 执行 Erie 风格的启发式静态 lint 检查。"""

# 启用更直接的前向引用标注写法。
from __future__ import annotations

# 标准库依赖分别负责正则解析、数据建模和路径读取。
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 静态 lint 问题对象负责承载严重级别、位置和规则编号。
@dataclass(frozen=True)
class StaticLintIssue:
    """
    表示单条静态 lint 发现。

    参数:
        severity: 问题严重级别，通常为 `error` 或 `warning`。
        message: 提供给上层报告器的人类可读说明文本。
        path: 发现问题的相对文件路径。
        line: 问题所在的 1 基行号。
        source: 问题来源类别，默认归入当前模块问题。
        code: 规则编号或问题标签。

    返回:
        无；实例化后的字段由上层报告器直接消费。
    """

    # severity 用来区分 error 与 warning 级别。
    severity: str  # 问题严重级别

    # message 保存给报告层展示的人类可读描述。
    message: str  # 问题说明文本

    # path 指向发现问题的相对 RTL 文件路径。
    path: str  # 问题所在文件路径

    # line 记录问题命中的 1 基源码行号。
    line: int  # 问题所在行号

    # source 标识问题归属的来源分类。
    source: str = "current_module_issue"  # 问题来源类别

    # code 保存 lint 规则编号或问题标签。
    code: str = "ASIC"  # 问题代码标签

    # 把 dataclass 字段导出成普通字典，供 JSON 报告层复用。
    def to_dict(self) -> dict[str, Any]:
        """
        返回适合序列化的普通字典。

        参数:
            无；当前方法直接读取实例字段。

        返回:
            以字段名为 key 的普通字典。
        """

        # 汇总当前问题对象的全部可序列化字段。
        dict_issue = {
            "severity": self.severity,  # 当前问题的严重级别
            "message": self.message,  # 当前问题的人类可读说明
            "path": self.path,  # 当前问题对应的文件路径
            "line": self.line,  # 当前问题对应的 1 基行号
            "source": self.source,  # 当前问题对应的来源类别
            "code": self.code,  # 当前问题对应的规则标签
        }  # 供 JSON 报告直接消费的字典结果

        # 返回当前问题的普通字典表示。
        return dict_issue

# 扫描 RTL 根目录下的 Verilog 文件并汇总静态 lint 问题。
def lint_generated_rtl(
    dict_spec: dict[str, Any],
    path_root: Path,
) -> list[StaticLintIssue]:
    """
    只对 RTL 源文件执行轻量 ASIC 导向静态检查。

    参数:
        dict_spec: 当前任务的规格字典，包含时钟和接口端口信息。
        path_root: 需要扫描的 RTL 根目录路径。

    返回:
        按文件顺序汇总得到的静态 lint 问题列表。
    """

    # 先从规格里抽取所有可信时钟名，供时钟类规则复用。
    set_clock_names = _clock_names(dict_spec)  # 规格中确认存在的时钟名称集合

    # 汇总所有文件命中的 lint 问题。
    list_issues: list[StaticLintIssue] = []  # 整个扫描阶段累计的问题列表

    # 顺序扫描根目录下的全部 Verilog 文件。
    for path_source in sorted(path_root.glob("**/*.v")):

        # testbench 文件不属于当前 RTL 本体静态检查范围。
        if _is_testbench(path_source):

            # 直接跳过当前 testbench 文件。
            continue

        # 先把文件路径转换成相对路径，供问题报告稳定引用。
        str_rel_path = path_source.relative_to(path_root).as_posix()  # 当前文件的相对路径字符串

        # 读取当前 Verilog 文件的原始文本内容。
        str_source_text = path_source.read_text(  # 当前 Verilog 文件的完整原始文本
            encoding="utf-8",  # 优先按 UTF-8 读取生成产物
            errors="ignore",  # 遇到异常字节时忽略，避免阻断扫描
        )

        # 先剥离块注释和行尾注释，供后续规则统一复用。
        list_stripped_lines = [
            _strip_line_comments(str_line)  # 单行注释剥离后的有效源码内容
            for str_line in _strip_block_comments(str_source_text).splitlines()  # 去掉块注释后的逐行文本
        ]  # 当前文件用于 lint 规则判断的净化源码行列表

        # 提前收集声明宽度信息，供位宽规则复用。
        dict_widths = _declared_widths(list_stripped_lines)  # 当前文件中各标识符的声明位宽表

        # 提前收集参数和 localparam 名称，供常量边界规则复用。
        set_constants = _constant_names(list_stripped_lines)  # 当前文件中被视为常量的名字集合

        # 提前收集各网表声明种类，供驱动规则判断 wire/reg 冲突。
        dict_declarations = _declared_net_kinds(list_stripped_lines)  # 当前文件中各信号的声明类别表

        # 汇总依赖当前文件上下文的全部检查批次。
        list_issue_batches = [
            # 先跑禁止性结构与驱动一致性检查。
            _function_task_issues(str_rel_path, list_stripped_lines),  # function/task 禁止规则命中的问题
            _driver_issues(str_rel_path, list_stripped_lines, dict_declarations),  # 驱动冲突与 wire 过程赋值问题
            _case_default_issues(str_rel_path, list_stripped_lines),  # 缺失 default 分支问题
            _case_default_xz_issues(str_rel_path, list_stripped_lines),  # default 分支驱动 x/z 的问题

            # 再跑 case 与 always 语义风格约束。
            _casex_casez_issues(str_rel_path, list_stripped_lines),  # 使用 casex/casez 的问题
            _legacy_sensitivity_issues(str_rel_path, list_stripped_lines),  # 组合逻辑敏感列表不完整的问题
            _sensitivity_separator_issues(str_rel_path, list_stripped_lines),  # 敏感列表误用或分隔符的问题
            _mixed_assignment_issues(str_rel_path, list_stripped_lines),  # always 块混用阻塞与非阻塞赋值的问题
            _assignment_style_issues(str_rel_path, list_stripped_lines),  # 时序/组合赋值风格不匹配的问题

            # 最后补齐时钟、位宽和仿真结构类检查。
            _raw_gated_clock_issues(str_rel_path, list_stripped_lines, set_clock_names),  # 直接门控时钟逻辑问题
            _derived_clock_issues(str_rel_path, list_stripped_lines, set_clock_names),  # 可疑派生时钟使用问题
            _xz_literal_issues(str_rel_path, list_stripped_lines),  # 显式 x/z 字面量问题
            _wire_initialization_issues(str_rel_path, list_stripped_lines),  # wire 声明即赋值的问题

            # 位宽匹配和仿真结构检查放在最后收口。
            _simple_width_issues(str_rel_path, list_stripped_lines, dict_widths),  # 简单位宽匹配问题
            _literal_base_width_issues(str_rel_path, list_stripped_lines),  # 常量缺显式位宽和进制的问题
            _for_loop_bound_issues(str_rel_path, list_stripped_lines, set_constants),  # for 循环边界非常量的问题
            _simulation_construct_issues(str_rel_path, list_stripped_lines),  # 仿真专用结构误入 RTL 的问题
        ]  # 当前文件所有检查器返回的问题批次

        # 顺序把每个检查器命中的问题批次并入总结果。
        for list_issue_batch in list_issue_batches:

            # 把当前检查器产出的全部问题追加到总列表。
            list_issues.extend(list_issue_batch)

    # 返回扫描得到的全部静态 lint 问题。
    return list_issues

# 从声明语句中提取每个信号被声明成 wire/reg/logic 的种类。
def _declared_net_kinds(list_lines: list[str]) -> dict[str, str]:
    """
    返回信号到声明种类的映射表。

    参数:
        list_lines: 已去掉注释后的源码行列表。

    返回:
        以信号名为 key、以声明种类为 value 的字典。
    """

    # 汇总当前文件中每个信号首次可识别的声明种类。
    dict_declarations: dict[str, str] = {}  # 信号名到声明类别的映射表

    # 先把声明片段正则拆成多段，避免单行模式过长且不易解释。
    tuple_decl_pattern_parts = (
        r"\b(?:(input|output|inout)\s+)?",  # 可选方向关键字
        r"(?:(wire|reg|logic)\s+)?",  # 可选网表类型关键字
        r"(?:signed\s+)?",  # 可选 signed 修饰
        r"(?:\[[^\]]+\]\s+)?",  # 可选位宽范围
        r"([A-Za-z_][A-Za-z0-9_]*)",  # 真正的信号名
    )  # 声明片段正则的顺序段列表

    # 再把多段片段拼成完整的声明匹配模式。
    str_decl_pattern = "".join(tuple_decl_pattern_parts)  # 完整的声明片段正则源码

    # 最终把声明片段模式编译成可复用的正则对象。
    pattern_decl: re.Pattern[str] = re.compile(str_decl_pattern)  # 匹配声明片段里的方向、网表类型和信号名

    # 顺序扫描净化后的每一行声明文本。
    for str_line in list_lines:

        # 先去掉首尾空白和末尾分号，便于后续切分片段。
        str_stripped_line = str_line.strip().rstrip(";")  # 去掉尾分号后的候选声明行

        # 没有声明关键字的普通语句不参与网表种类提取。
        if not re.search(r"\b(input|output|inout|wire|reg|logic)\b", str_stripped_line):

            # 直接跳过当前非声明行。
            continue

        # 逗号分隔的声明行需要逐段提取信号名和类型。
        for str_fragment in str_stripped_line.split(","):

            # 先匹配当前片段里的方向、类型和名字。
            match_decl: re.Match[str] | None = pattern_decl.search(  # 当前声明片段的正则匹配结果
                str_fragment.strip()  # 去掉片段首尾空白后的声明文本
            )

            # 未匹配成功的片段无法稳定提取信号名。
            if not match_decl:

                # 直接跳过当前无法解析的片段。
                continue

            # 解包当前匹配得到的方向、网表类型和信号名。
            str_direction, str_net_type, str_name = match_decl.groups()  # 当前片段抽取出的声明信息

            # 关键字自身不应被误登记成真实信号名。
            if str_name in {"input", "output", "inout", "wire", "reg", "logic"}:

                # 跳过当前误命中的关键字片段。
                continue

            # 没显式类型时按原逻辑回退到 wire。
            dict_declarations[str_name] = str_net_type or "wire"  # 无显式类型时按 wire 记录该信号

    # 返回信号到声明种类的映射表。
    return dict_declarations

# 检查同一信号是否存在多驱动或 wire 过程赋值问题。
def _driver_issues(
    str_rel_path: str,
    list_lines: list[str],
    dict_declarations: dict[str, str],
) -> list[StaticLintIssue]:
    """
    返回驱动相关问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。
        dict_declarations: 当前文件中各信号的声明种类表。

    返回:
        当前文件命中的驱动类问题列表。
    """

    # 汇总驱动规则命中的全部问题。
    list_issues: list[StaticLintIssue] = []  # 这里累计驱动冲突与 wire 过程赋值两类诊断

    # 为每个信号记录连续赋值或过程赋值的来源位置。
    dict_drivers: dict[str, list[tuple[str, int]]] = {}  # 信号名到驱动来源与行号的映射表

    # 顺序扫描每一行，识别驱动语句类型。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 先去掉首尾空白，便于做 assign 和过程赋值匹配。
        str_stripped_line = str_line.strip()  # 当前源码行的去空白版本

        # 先判断当前行是否是连续 assign 语句。
        match_assign: re.Match[str] | None = re.match(r"\bassign\s+([A-Za-z_][A-Za-z0-9_]*)\s*=", str_stripped_line)  # 匹配 assign 形式的连续驱动左值

        # 连续 assign 命中时直接登记 continuous 驱动来源。
        if match_assign:

            # 把当前信号的连续驱动记录到驱动表里。
            dict_drivers.setdefault(match_assign.group(1), []).append(("continuous", int_index))  # 把连续驱动来源挂到该信号名下

            # 当前行已经按连续驱动处理完毕。
            continue

        # 再判断当前行是否是过程赋值语句。
        match_procedural: re.Match[str] | None = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:<=|=)\s*", str_stripped_line)  # 匹配过程赋值左值

        # 没命中过程赋值时无需继续处理。
        if not match_procedural:

            # 当前行不是驱动语句，直接跳过。
            continue

        # 提取过程赋值左值信号名。
        str_signal_name = match_procedural.group(1)  # 当前过程赋值左值信号名

        # 控制关键字命中时不应误算成信号名。
        if str_signal_name in {"if", "case", "for", "while", "assign"}:

            # 跳过当前关键字误命中的伪信号。
            continue

        # 把当前过程赋值登记到驱动来源表中。
        dict_drivers.setdefault(str_signal_name, []).append(("procedural", int_index))  # 把过程赋值来源挂到该信号名下

        # wire 类型信号不允许被过程赋值直接驱动。
        if dict_declarations.get(str_signal_name) == "wire":

            # 记录当前 wire 被过程赋值驱动的问题。
            list_issues.append(
                StaticLintIssue(
                    "error",
                    (
                        f"Signal {str_signal_name!r} is declared as wire but "
                        "assigned procedurally; use reg/logic or continuous assign."
                    ),
                    str_rel_path,
                    int_index,
                    code="WIRE_PROCEDURAL_ASSIGN",
                )
            )

    # 再根据驱动来源表检查多驱动冲突。
    for str_signal_name, list_signal_drivers in dict_drivers.items():

        # 当前信号有哪些驱动语义来源需要先压平成集合。
        set_driver_kinds = {str_driver_kind for str_driver_kind, _ in list_signal_drivers}  # 当前信号涉及过的驱动类别集合

        # 多驱动或混合驱动类型都会构成冲突。
        if len(list_signal_drivers) > 1 and (
            "continuous" in set_driver_kinds or len(set_driver_kinds) > 1
        ):

            # 记录当前信号的多驱动冲突问题。
            list_issues.append(
                StaticLintIssue(
                    "error",
                    (
                        f"Signal {str_signal_name!r} has multiple drivers; "
                        "keep exactly one driver source per net."
                    ),
                    str_rel_path,
                    list_signal_drivers[0][1],
                    code="MULTIPLE_DRIVERS",
                )
            )

    # 把驱动一致性诊断结果交回主扫描流程。
    return list_issues

# 检查是否在生成 RTL 中出现 function 或 task 块。
def _function_task_issues(
    str_rel_path: str,
    list_lines: list[str],
) -> list[StaticLintIssue]:
    """
    返回 function/task 违规问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。

    返回:
        当前文件命中的 function/task 禁止规则问题列表。
    """

    # 汇总 function/task 相关规则命中的问题。
    list_issues: list[StaticLintIssue] = []  # 这里保存 RTL 中出现子程序抽象的违规记录

    # 顺序扫描每一行，查找 function 或 task 关键字。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 先定位当前行是否包含 function 或 task 块声明。
        match_block: re.Match[str] | None = re.search(r"\b(function|task)\b", str_line)  # 定位 function 或 task 关键字

        # 命中 function 或 task 时才需要登记问题。
        if match_block:

            # function 场景需要给出联动到函数限制规则的说明。
            if match_block.group(1) == "function":

                # function 会把可审阅的内联逻辑藏进子程序抽象里。
                str_message = (
                    "Verilog function blocks are not allowed in generated RTL; "
                    "MUST_FUNC_NO_RECURSION and MUST_FUNC_NO_NONBLOCKING keep "
                    "generated logic inline and reviewable."
                )  # 这条文案强调 function 会破坏生成逻辑的内联可审阅性

            # task 场景需要强调时序控制风险。
            else:

                # task 会引入时序控制入口，和当前可综合约束直接冲突。
                str_message = (
                    "Verilog task blocks are not allowed in generated RTL; "
                    "MUST_TASK_NO_TIMING_CONTROL keeps generated logic free of "
                    "task timing hazards."
                )  # 这条文案强调 task 会把时序控制带进生成 RTL

            # 记录当前 function/task 违规问题。
            list_issues.append(
                StaticLintIssue(
                    "error",
                    str_message,
                    str_rel_path,
                    int_index,
                    code="NO_TASK_FUNCTION",
                )
            )

    # 把子程序抽象违规项交回主扫描流程。
    return list_issues

# 检查每个 case 语句是否都显式提供了 default 分支。
def _case_default_issues(
    str_rel_path: str,
    list_lines: list[str],
) -> list[StaticLintIssue]:
    """
    返回缺失 default 分支的问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。

    返回:
        当前文件命中的 case-default 问题列表。
    """

    # 汇总 case 缺省分支规则命中的问题。
    list_issues: list[StaticLintIssue] = []  # 这里汇总缺少 default 兜底分支的 case 诊断

    # 用栈跟踪嵌套 case 的起始行和 default 覆盖状态。
    list_case_stack: list[dict[str, Any]] = []  # 正在打开的 case 语句状态栈

    # 顺序扫描每一行，维护当前 case 栈状态。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 命中新 case 头时需要把其状态压栈。
        if re.search(r"\bcase[xz]?\s*\(", str_line):

            # 记录当前 case 的起始行和 default 初始状态。
            list_case_stack.append(
                {"line": int_index, "has_default": False}
            )

        # 命中 default 标签时把当前最内层 case 标记为已覆盖。
        if list_case_stack and re.search(r"\bdefault\s*:", str_line):

            # 更新当前最内层 case 的 default 覆盖标记。
            list_case_stack[-1]["has_default"] = True  # 标记当前最内层 case 已经出现 default

        # 命中 endcase 时需要关闭当前最内层 case。
        if re.search(r"\bendcase\b", str_line) and list_case_stack:

            # 弹出当前结束的 case 状态，判断其是否缺失 default。
            dict_case_state = list_case_stack.pop()  # 当前结束的 case 状态对象

            # 缺少 default 分支时需要登记问题。
            if not dict_case_state["has_default"]:

                # 记录当前 case 缺失 default 分支的问题。
                list_issues.append(
                    StaticLintIssue(
                        "error",
                        (
                            "Case statement has no default branch; "
                            "MUST_CASE_HAS_DEFAULT requires an explicit safe default."
                        ),
                        str_rel_path,
                        int(dict_case_state["line"]),
                        code="CASE_DEFAULT",
                    )
                )

    # 把缺失 default 的 case 诊断交回主扫描流程。
    return list_issues

# 检查 default 分支是否驱动了不确定的 x/z 值。
def _case_default_xz_issues(
    str_rel_path: str,
    list_lines: list[str],
) -> list[StaticLintIssue]:
    """
    返回 default 分支驱动 x/z 的问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。

    返回:
        当前文件命中的 default-x/z 问题列表。
    """

    # 汇总 default 分支驱动 x/z 的问题。
    list_issues: list[StaticLintIssue] = []  # 这里记录 default 分支驱动不确定值的命中项

    # 顺序扫描每一行，识别 default 分支里的不确定值。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 命中 default 分支驱动 x/z 字面量时需要登记问题。
        if re.search(
            r"\bdefault\s*:\s*[^;]*\d+\s*'\s*[bBoOdDhH][0-9a-fA-F_xXzZ]*[xXzZ]",
            str_line,
        ):

            # 记录当前 default 分支驱动 x/z 的警告。
            list_issues.append(
                StaticLintIssue(
                    "warning",
                    (
                        "REC_CASE_DEFAULT_NOT_XZ requires case default branches "
                        "to drive deterministic non-x/z values."
                    ),
                    str_rel_path,
                    int_index,
                    code="CASE_DEFAULT_XZ",
                )
            )

    # 把 default 驱动不确定值的命中项交回主扫描流程。
    return list_issues

# 检查是否使用了 casex 或 casez 这类弱确定性匹配。
def _casex_casez_issues(
    str_rel_path: str,
    list_lines: list[str],
) -> list[StaticLintIssue]:
    """
    返回 casex/casez 使用问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。

    返回:
        当前文件命中的 casex/casez 问题列表。
    """

    # 汇总 casex/casez 相关问题。
    list_issues: list[StaticLintIssue] = []  # 这里承接弱确定性 case 匹配写法的告警

    # 顺序扫描每一行，识别 casex 或 casez 语句。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 命中 casex/casez 时需要登记可维护性警告。
        if re.search(r"\bcase[xz]\s*\(", str_line):

            # 记录当前 casex/casez 使用警告。
            list_issues.append(
                StaticLintIssue(
                    "warning",
                    (
                        "REC_CASE_NO_CASEX_CASEZ prefers plain case for "
                        "deterministic simulation."
                    ),
                    str_rel_path,
                    int_index,
                    code="CASEX_CASEZ",
                )
            )

    # 把 casex/casez 使用痕迹交回主扫描流程。
    return list_issues

# 检查组合 always 是否仍在使用不完整的旧式敏感列表。
def _legacy_sensitivity_issues(
    str_rel_path: str,
    list_lines: list[str],
) -> list[StaticLintIssue]:
    """
    返回旧式敏感列表问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。

    返回:
        当前文件命中的敏感列表完整性问题列表。
    """

    # 汇总旧式敏感列表规则命中的问题。
    list_issues: list[StaticLintIssue] = []  # 这里积累组合逻辑敏感列表覆盖不全的诊断

    # 顺序扫描每一行 always 头，检查敏感列表是否完整。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 先抽取 always 头里的敏感列表文本。
        match_sensitivity: re.Match[str] | None = re.search(r"\balways\s*@\s*\(([^)]*)\)", str_line)  # 抓取 always 头中的敏感列表正文

        # 没有 always 头时无需继续分析敏感列表。
        if not match_sensitivity:

            # 这一行没有 always 头，因此不参与敏感列表完整性判断。
            continue

        # 提取当前 always 头中的敏感列表正文。
        str_sensitivity_text = match_sensitivity.group(1).strip()  # 当前敏感列表正文

        # 把敏感列表统一转成小写，便于判断边沿触发关键字。
        str_lowered_sensitivity = str_sensitivity_text.lower()  # 当前敏感列表的小写版本

        # `*` 或边沿触发列表都不属于旧式不完整敏感列表。
        if (
            "*" in str_sensitivity_text
            or "posedge" in str_lowered_sensitivity
            or "negedge" in str_lowered_sensitivity
        ):

            # 当前敏感列表属于允许形式，不登记问题。
            continue

        # 记录当前旧式敏感列表问题。
        list_issues.append(
            StaticLintIssue(
                "warning",
                (
                    "MUST_SENS_LIST_COMPLETE_MINIMAL requires complete "
                    "sensitivity coverage; use always @(*) for combinational logic."
                ),
                str_rel_path,
                int_index,
                code="ALWAYS_STAR",
            )
        )

    # 把旧式敏感列表诊断交回主扫描流程。
    return list_issues

# 检查 always 敏感列表里是否误用了 `|` 或 `||` 分隔符。
def _sensitivity_separator_issues(
    str_rel_path: str,
    list_lines: list[str],
) -> list[StaticLintIssue]:
    """
    返回敏感列表分隔符问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。

    返回:
        当前文件命中的敏感列表分隔符问题列表。
    """

    # 汇总敏感列表分隔符规则命中的问题。
    list_issues: list[StaticLintIssue] = []  # 这里登记敏感列表误写成逻辑或分隔的错误

    # 顺序扫描每一行 always 头，检查敏感列表文本。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 这里只关心括号内部的信号分隔方式，因此先截出敏感列表正文。
        match_sensitivity: re.Match[str] | None = re.search(r"\balways\s*@\s*\(([^)]*)\)", str_line)  # 提取分隔符诊断要分析的敏感列表正文

        # 没有 always 头时无需继续分析。
        if not match_sensitivity:

            # 没有敏感列表头的普通语句不需要检查分隔符写法。
            continue

        # 取出当前敏感列表正文，检查是否混入或分隔符。
        str_sensitivity_text = match_sensitivity.group(1)  # 当前 always 头的敏感列表正文

        # 命中 `|` 或 `||` 时需要登记错误。
        if "|" in str_sensitivity_text:

            # 记录当前敏感列表误用分隔符的问题。
            list_issues.append(
                StaticLintIssue(
                    "error",
                    (
                        "MUST_SENS_NO_OR_SEPARATOR forbids `|` or `||` in "
                        "always sensitivity lists; list signals directly or use "
                        "always @(*)."
                    ),
                    str_rel_path,
                    int_index,
                    code="SENS_OR_SEPARATOR",
                )
            )

    # 把敏感列表分隔符错误交回主扫描流程。
    return list_issues

# 检查同一个 always 块里是否混用了阻塞赋值和非阻塞赋值。
def _mixed_assignment_issues(
    str_rel_path: str,
    list_lines: list[str],
) -> list[StaticLintIssue]:
    """
    返回混合赋值风格问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。

    返回:
        当前文件命中的混合阻塞/非阻塞赋值问题列表。
    """

    # 汇总 always 块混合赋值规则命中的问题。
    list_issues: list[StaticLintIssue] = []  # 这里保存同一 always 块混用赋值风格的结果

    # 顺序扫描每个 always 代码块，判断内部赋值风格是否混用。
    for int_start_line, list_block_lines in _always_blocks(list_lines):

        # 先统计 always 体内是否出现裸等号赋值，后面才能判断是否混用。
        bool_has_blocking = any(re.search(r"(?<![<>=!])=(?!=)", str_block_line) for str_block_line in list_block_lines)  # 当前块里是否出现过阻塞赋值

        # 这里单独统计 `<=` 的出现情况，用来识别时序写法是否渗入当前块。
        bool_has_nonblocking = any("<=" in str_block_line for str_block_line in list_block_lines)  # 当前块里是否混入了非阻塞赋值

        # 阻塞和非阻塞同时存在时需要登记警告。
        if bool_has_blocking and bool_has_nonblocking:

            # 记录当前 always 块混合赋值风格的问题。
            list_issues.append(
                StaticLintIssue(
                    "warning",
                    (
                        "MUST_COMB_BLOCKING_ASSIGN and "
                        "MUST_SEQ_NONBLOCKING_ASSIGN require separated "
                        "combinational and sequential assignment intent."
                    ),
                    str_rel_path,
                    int_start_line,
                    code="MIXED_ASSIGN",
                )
            )

    # 把混合赋值风格诊断交回主扫描流程。
    return list_issues

# 检查组合块和时序块是否分别使用了正确的赋值形式。
def _assignment_style_issues(
    str_rel_path: str,
    list_lines: list[str],
) -> list[StaticLintIssue]:
    """
    返回赋值风格与 always 类型不匹配的问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。

    返回:
        当前文件命中的赋值风格问题列表。
    """

    # 汇总时序/组合赋值风格问题。
    list_issues: list[StaticLintIssue] = []  # 这里追踪 always 类型与赋值语义不匹配的诊断

    # 顺序检查每个 always 代码块的头和内部赋值形式。
    for int_start_line, list_block_lines in _always_blocks(list_lines):

        # 当前 always 块首行决定它是时序块还是组合块。
        str_header_line = list_block_lines[0]  # 当前 always 块的头部语句

        # 通过 posedge/negedge 判断当前块是否是时序块。
        bool_sequential = bool(re.search(r"\b(posedge|negedge)\b", str_header_line, flags=re.IGNORECASE))  # 当前 always 块是否属于边沿触发时序逻辑

        # 时序块里出现阻塞赋值时需要报错。
        if bool_sequential:

            # 时序块必须全部使用非阻塞赋值。
            if any(_has_blocking_assignment(str_block_line) for str_block_line in list_block_lines):

                # 记录当前时序块误用阻塞赋值的问题。
                list_issues.append(
                    StaticLintIssue(
                        "error",
                        (
                            "MUST_SEQ_NONBLOCKING_ASSIGN requires nonblocking "
                            "assignments in sequential always blocks."
                        ),
                        str_rel_path,
                        int_start_line,
                        code="SEQ_BLOCKING_ASSIGN",
                    )
                )

        # 组合块里出现非阻塞赋值同样需要报错。
        elif any("<=" in str_block_line for str_block_line in list_block_lines):

            # 记录当前组合块误用非阻塞赋值的问题。
            list_issues.append(
                StaticLintIssue(
                    "error",
                    (
                        "MUST_COMB_BLOCKING_ASSIGN requires blocking "
                        "assignments in combinational always blocks."
                    ),
                    str_rel_path,
                    int_start_line,
                    code="COMB_NONBLOCKING_ASSIGN",
                )
            )

    # 把赋值风格不匹配的诊断交回主扫描流程。
    return list_issues

# 检查是否存在直接对时钟做与或运算得到的门控时钟逻辑。
def _raw_gated_clock_issues(
    str_rel_path: str,
    list_lines: list[str],
    set_clock_names: set[str],
) -> list[StaticLintIssue]:
    """
    返回原始门控时钟问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。
        set_clock_names: 规格里确认存在的时钟名称集合。

    返回:
        当前文件命中的原始门控时钟问题列表。
    """

    # 汇总原始门控时钟规则命中的问题。
    list_issues: list[StaticLintIssue] = []  # 这里登记直接拼接门控时钟逻辑的高风险命中

    # 先按名字长度排序，避免短时钟名在 alternation 里抢先吞掉长名字。
    list_sorted_clock_names = sorted(set_clock_names, key=len, reverse=True)  # 长名字优先的时钟名顺序表

    # 再拼出供门控时钟规则复用的联合匹配模式。
    str_clock_pattern = "|".join(re.escape(str_clock_name) for str_clock_name in list_sorted_clock_names)  # 供门控时钟正则复用的时钟名联合模式

    # 没有可信时钟名时，时钟相关规则无法稳定执行。
    if not str_clock_pattern:

        # 当前缺少可信时钟名，直接返回空结果以避免误报。
        return list_issues

    # 这些模式分别覆盖 assign/wire 形式的常见门控时钟命名。
    tuple_patterns = (
        rf"\bassign\s+\w*(?:gclk|gated_clk|clk_gated|clock_gated)\w*"
        rf"\s*=\s*[^;]*\b(?:{str_clock_pattern})\b\s*[&|]",
        rf"\bassign\s+\w*(?:gclk|gated_clk|clk_gated|clock_gated)\w*"
        rf"\s*=\s*[^;]*[&|]\s*\b(?:{str_clock_pattern})\b",
        rf"\bwire\s+\w*(?:gclk|gated_clk|clk_gated|clock_gated)\w*"
        rf"\s*=\s*[^;]*\b(?:{str_clock_pattern})\b\s*[&|]",
        rf"\bwire\s+\w*(?:gclk|gated_clk|clk_gated|clock_gated)\w*"
        rf"\s*=\s*[^;]*[&|]\s*\b(?:{str_clock_pattern})\b",
    )  # 原始门控时钟常见写法的匹配模式集合

    # 顺序扫描每一行，检查是否命中任一门控时钟模式。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 命中任一模式时需要登记错误。
        if any(
            re.search(str_pattern, str_line)  # 当前门控时钟模式在本行的匹配结果
            for str_pattern in tuple_patterns
        ):

            # 记录当前原始门控时钟问题。
            list_issues.append(
                StaticLintIssue(
                    "error",
                    (
                        "MUST_CLK_NO_COMB_CLOCK forbids raw gated clock logic; "
                        "use clock-enable RTL or an approved ICG wrapper."
                    ),
                    str_rel_path,
                    int_index,
                    code="RAW_GATED_CLOCK",
                )
            )

    # 把原始门控时钟诊断交回主扫描流程。
    return list_issues

# 检查 always 头里是否使用了未在规格中确认的派生时钟样命名。
def _derived_clock_issues(
    str_rel_path: str,
    list_lines: list[str],
    set_clock_names: set[str],
) -> list[StaticLintIssue]:
    """
    返回派生时钟样信号问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。
        set_clock_names: 规格里确认存在的时钟名称集合。

    返回:
        当前文件命中的派生时钟问题列表。
    """

    # 汇总派生时钟样信号问题。
    list_issues: list[StaticLintIssue] = []  # 这里记录边沿触发头里未确认的时钟样信号

    # 先准备边沿触发 always 头的提取模式，避免单行匹配式过长。
    str_edge_clock_pattern = (
        r"\balways\s*@\s*\([^)]*\b(?:posedge|negedge)\s+"  # always 头到边沿关键字
        r"([A-Za-z_][A-Za-z0-9_]*)"  # 真正的边沿触发信号名
    )  # 边沿触发信号提取模式

    # 顺序扫描每一行 always 头，定位边沿触发信号名。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 先匹配 always 头里 posedge/negedge 后面的信号名。
        match_clock: re.Match[str] | None = re.search(str_edge_clock_pattern, str_line)  # 提取 always 头里真正参与边沿触发的信号名

        # 没命中边沿触发信号名时无需继续分析。
        if not match_clock:

            # 不是边沿触发 always 头的语句不会贡献派生时钟诊断。
            continue

        # 取出当前边沿触发信号名。
        str_clock_like_name = match_clock.group(1)  # 当前 always 头使用的边沿触发信号名

        # 名字像时钟但不在规格时钟集合里时需要报错。
        if str_clock_like_name not in set_clock_names and (
            "clk" in str_clock_like_name.lower()
            or "clock" in str_clock_like_name.lower()
        ):

            # 记录当前派生时钟样信号问题。
            list_issues.append(
                StaticLintIssue(
                    "error",
                    (
                        "MUST_CLK_NO_COMB_CLOCK and MUST_CLK_NO_REGOUT_CLOCK "
                        "require confirmed clock ports, not derived clock-like signals."
                    ),
                    str_rel_path,
                    int_index,
                    code="DERIVED_CLOCK",
                )
            )

    # 把派生时钟样信号诊断交回主扫描流程。
    return list_issues

# 检查逻辑表达式里是否显式写入了 x/z 字面量。
def _xz_literal_issues(
    str_rel_path: str,
    list_lines: list[str],
) -> list[StaticLintIssue]:
    """
    返回显式 x/z 字面量问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。

    返回:
        当前文件命中的 x/z 字面量问题列表。
    """

    # 汇总显式 x/z 字面量问题。
    list_issues: list[StaticLintIssue] = []  # 这里收拢显式 x/z 字面量造成的不确定逻辑诊断

    # 顺序扫描每一行逻辑文本，定位包含 x/z 的常量。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 命中 x/z 字面量时需要登记错误。
        if re.search(
            r"\d+\s*'\s*[bBoOdDhH][0-9a-fA-F_xXzZ]*[xXzZ][0-9a-fA-F_xXzZ]*",
            str_line,
        ):

            # 记录当前显式 x/z 字面量问题。
            list_issues.append(
                StaticLintIssue(
                    "error",
                    (
                        "MUST_OP_NO_XZ_ARITH, MUST_OP_NO_XZ_CONDITION, and "
                        "MUST_BRANCH_COND_NO_XZ forbid explicit x/z values in "
                        "generated RTL logic."
                    ),
                    str_rel_path,
                    int_index,
                    code="XZ_LITERAL",
                )
            )

    # 把 x/z 字面量诊断交回主扫描流程。
    return list_issues

# 检查 wire 声明是否直接和赋值写在同一条语句里。
def _wire_initialization_issues(
    str_rel_path: str,
    list_lines: list[str],
) -> list[StaticLintIssue]:
    """
    返回 wire 声明即赋值的问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。

    返回:
        当前文件命中的 wire 初始化问题列表。
    """

    # 汇总 wire 声明即赋值问题。
    list_issues: list[StaticLintIssue] = []  # 这里记录 wire 声明与赋值耦合到同一句的错误

    # 顺序扫描每一行，检查 wire 声明里是否夹带赋值。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 命中 wire 声明即赋值时需要登记错误。
        if re.search(r"\bwire\b[^;]*=", str_line):

            # 记录当前 wire 声明即赋值问题。
            list_issues.append(
                StaticLintIssue(
                    "error",
                    (
                        "MUST_ASSIGN_WIDTH_MATCH-compatible review requires "
                        "wires to be declared separately from standalone assign statements."
                    ),
                    str_rel_path,
                    int_index,
                    code="WIRE_INIT",
                )
            )

    # 把 wire 初始化写法诊断交回主扫描流程。
    return list_issues

# 检查简单赋值和关系比较表达式的位宽是否匹配。
def _simple_width_issues(
    str_rel_path: str,
    list_lines: list[str],
    dict_widths: dict[str, int],
) -> list[StaticLintIssue]:
    """
    返回简单位宽匹配问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。
        dict_widths: 当前文件中各标识符的声明位宽表。

    返回:
        当前文件命中的位宽匹配问题列表。
    """

    # 汇总位宽匹配规则命中的问题。
    list_issues: list[StaticLintIssue] = []  # 这里汇总简单赋值和比较表达式的位宽冲突

    # 顺序扫描每一行，检查 assign 和关系表达式。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 先尝试匹配简单 assign 赋值语句。
        match_assign: re.Match[str] | None = re.search(r"\bassign\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]+);", str_line)  # 提取 assign 语句左右两侧的简单表达式

        # 命中简单 assign 时需要先检查左右位宽。
        if match_assign:

            # 先取出 assign 左值表达式，后面要据此查询声明位宽。
            str_left_expr = match_assign.group(1)  # assign 左值表达式文本

            # 再取出 assign 右值表达式，后面要据此推断表达式位宽。
            str_right_expr = match_assign.group(2).strip()  # assign 右值表达式文本

            # 把 assign 左右值的位宽问题并入结果列表。
            list_issues.extend(
                _width_pair_issue(
                    str_rel_path,
                    int_index,
                    str_left_expr,
                    str_right_expr,
                    dict_widths,
                    "ASSIGN_WIDTH",
                )
            )

        # 只有关系判断或条件表达式所在行才值得继续做位宽比较。
        if re.search(r"\b(if|while)\s*\(|\?", str_line):

            # 先准备关系比较式模式，避免单行匹配式过长且难以核对。
            str_relation_pattern = (
                r"([A-Za-z_][A-Za-z0-9_]*|\d+\s*'\s*[bBoOdDhH][0-9a-fA-F_xXzZ]+)"  # 左侧简单操作数
                r"\s*(==|!=|<=|>=|<|>)\s*"  # 关系运算符
                r"([A-Za-z_][A-Za-z0-9_]*|\d+\s*'\s*[bBoOdDhH][0-9a-fA-F_xXzZ]+)"  # 右侧简单操作数
            )  # 关系比较式匹配模式

            # 先匹配关系比较式左右两侧的简单表达式。
            match_relation: re.Match[str] | None = re.search(str_relation_pattern, str_line)  # 提取关系比较式的左右简单操作数

            # 关系式匹配成功时再检查左右位宽是否一致。
            if match_relation:

                # 先取出关系式左侧表达式，后面要查询它的声明位宽。
                str_left_expr = match_relation.group(1)  # 关系式左侧表达式文本

                # 再取出关系式右侧表达式，后面要推断它的表达式位宽。
                str_right_expr = match_relation.group(3)  # 关系式右侧表达式文本

                # 把关系式位宽问题并入结果列表。
                list_issues.extend(
                    _width_pair_issue(
                        str_rel_path,
                        int_index,
                        str_left_expr,
                        str_right_expr,
                        dict_widths,
                        "REL_WIDTH",
                    )
                )

    # 把简单位宽冲突诊断交回主扫描流程。
    return list_issues

# 检查普通整数常量是否缺少显式位宽和进制。
def _literal_base_width_issues(
    str_rel_path: str,
    list_lines: list[str],
) -> list[StaticLintIssue]:
    """
    返回常量缺显式位宽和进制的问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。

    返回:
        当前文件命中的常量位宽/进制建议列表。
    """

    # 汇总常量显式位宽与进制建议问题。
    list_issues: list[StaticLintIssue] = []  # 常量位宽与进制建议列表

    # 顺序扫描每一行赋值文本，识别普通十进制裸常量。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 只要命中裸十进制常量且没有显式位宽进制，就登记建议。
        if re.search(r"=\s*\d+\s*;", str_line) and not re.search(
            r"\d+\s*'\s*[bBoOdDhH]",
            str_line,
        ):

            # 记录当前常量缺显式位宽和进制的建议。
            list_issues.append(
                StaticLintIssue(
                    "warning",
                    (
                        "REC_LITERAL_EXPLICIT_BASE_WIDTH prefers constants and "
                        "parameters with explicit width and base."
                    ),
                    str_rel_path,
                    int_index,
                    code="LITERAL_BASE_WIDTH",
                )
            )

    # 返回常量显式位宽和进制建议列表。
    return list_issues

# 比较两侧简单表达式的位宽，并在不一致时返回问题列表。
def _width_pair_issue(
    str_rel_path: str,
    int_line_number: int,
    str_left_expr: str,
    str_right_expr: str,
    dict_widths: dict[str, int],
    str_code: str,
) -> list[StaticLintIssue]:
    """
    返回一对简单表达式之间的位宽匹配问题。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        int_line_number: 需要登记问题的源码行号。
        str_left_expr: 左侧简单表达式文本。
        str_right_expr: 右侧简单表达式文本。
        dict_widths: 当前文件中各标识符的声明位宽表。
        str_code: 问题代码，决定使用的规则文案。

    返回:
        位宽不匹配时返回单元素问题列表，否则返回空列表。
    """

    # 先解析左侧表达式的可判定位宽。
    int_left_width = _expr_width(str_left_expr, dict_widths)  # 左侧表达式推断出的位宽

    # 再解析右侧表达式的可判定位宽。
    int_right_width = _expr_width(str_right_expr, dict_widths)  # 右侧表达式推断出的位宽

    # 任一侧不可判定或两侧位宽一致时不应登记问题。
    if (
        int_left_width is None
        or int_right_width is None
        or int_left_width == int_right_width
    ):

        # 返回空列表，表示当前比较对没有位宽问题。
        return []

    # 根据问题代码选择 assign 或关系比较规则名。
    str_rule_name = "MUST_ASSIGN_WIDTH_MATCH" if str_code == "ASSIGN_WIDTH" else "MUST_OP_REL_WIDTH_MATCH"  # 当前位宽问题应引用的规则名

    # 组装当前位宽不匹配的单条诊断并立即返回。
    return [
        StaticLintIssue(
            "error",
            (
                f"{str_rule_name} requires simple compared or assigned expressions "
                f"to use matching widths ({int_left_width} != {int_right_width})."
            ),
            str_rel_path,
            int_line_number,
            code=str_code,
        )
    ]

# 检查 for 循环边界和步进是否都落在常量表达式范围内。
def _for_loop_bound_issues(
    str_rel_path: str,
    list_lines: list[str],
    set_constants: set[str],
) -> list[StaticLintIssue]:
    """
    返回 for 循环边界非常量的问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。
        set_constants: 当前文件中被视为常量的名字集合。

    返回:
        当前文件命中的 for 循环边界问题列表。
    """

    # 汇总 for 循环边界规则命中的问题。
    list_issues: list[StaticLintIssue] = []  # 这里跟踪 for 边界不满足常量约束的违规项

    # 顺序扫描每一行，识别 for 头中的初始化、条件和步进。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 先尝试匹配完整的 for 头三元组。
        match_for: re.Match[str] | None = re.search(r"\bfor\s*\(([^;]+);([^;]+);([^)]+)\)", str_line)  # 提取 for 头里的初始化、条件和步进片段

        # 当前行不是 for 头时无需继续分析。
        if not match_for:

            # 直接跳过当前非 for 行。
            continue

        # 命中 for 头后，先把三段子句去空白，便于分别解析变量和值。
        str_init_clause, str_cond_clause, str_step_clause = (str_part.strip() for str_part in match_for.groups())  # 去空白后的 for 三元组文本

        # 解析初始化子句中的循环变量和值。
        match_init: re.Match[str] | None = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]+)", str_init_clause)  # 抽取初始化子句中的循环变量和值

        # 解析步进子句中被更新的循环变量。
        match_step: re.Match[str] | None = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", str_step_clause)  # 抽取步进子句里被更新的变量名

        # 初始化命中时提取循环变量名。
        str_loop_var = match_init.group(1) if match_init else ""  # 初始化子句中的循环变量名

        # 初始化命中时提取循环初值表达式。
        str_init_value = match_init.group(2).strip() if match_init else ""  # 初始化子句里的循环初值表达式

        # 先准备条件子句右侧边界的提取模式，避免单行匹配式过长。
        str_cond_bound_pattern = (
            r"(?:<=|>=|<|>)\s*"  # 关系运算符右侧的起点
            r"([A-Za-z_][A-Za-z0-9_]*|\d+\s*'\s*[bBoOdDhH][0-9a-fA-F_]+|\d+)"  # 允许的边界表达式
        )  # 条件子句右侧边界提取模式

        # 再从条件子句中提取右侧边界表达式。
        match_cond_rhs: re.Match[str] | None = re.search(str_cond_bound_pattern, str_cond_clause)  # 抽取条件子句右侧的循环边界表达式

        # 条件匹配成功时提取循环边界表达式。
        str_cond_bound = match_cond_rhs.group(1).strip() if match_cond_rhs else ""  # 条件子句里的边界表达式

        # 步进命中时提取被更新的变量名。
        str_step_var = match_step.group(1) if match_step else ""  # 步进子句中的目标变量名

        # 任一关键信息缺失或非常量时都需要登记错误。
        if (
            not str_loop_var
            or str_step_var != str_loop_var
            or not _is_constant_expr(str_init_value, set_constants)
            or not _is_constant_expr(str_cond_bound, set_constants)
        ):

            # 记录当前 for 循环边界不满足常量约束的问题。
            list_issues.append(
                StaticLintIssue(
                    "error",
                    (
                        "MUST_LOOP_FOR_CONST_BOUNDS requires constant for-loop "
                        "bounds and updates to the loop variable."
                    ),
                    str_rel_path,
                    int_index,
                    code="FOR_CONST_BOUNDS",
                )
            )

    # 把 for 常量边界诊断交回主扫描流程。
    return list_issues

# 检查仿真专用结构是否误入可综合 RTL 源码。
def _simulation_construct_issues(
    str_rel_path: str,
    list_lines: list[str],
) -> list[StaticLintIssue]:
    """
    返回仿真专用结构问题列表。

    参数:
        str_rel_path: 当前被扫描文件的相对路径。
        list_lines: 已去掉注释后的源码行列表。

    返回:
        当前文件命中的仿真专用结构问题列表。
    """

    # 汇总仿真专用结构规则命中的问题。
    list_issues: list[StaticLintIssue] = []  # 这里收集仿真专用结构混入 RTL 的诊断

    # 顺序扫描每一行，检查是否命中任一仿真专用结构。
    for int_index, str_line in enumerate(list_lines, start=1):

        # 逐个模式检查当前行是否命中仿真专用结构。
        for str_pattern, str_message in (
            (  # 抓取 simulation-only 的 initial 块
                r"\binitial\b",
                "MUST_INITIAL_FORBIDDEN keeps simulation-only initial blocks out "
                "of RTL source.",
            ),
            (  # 抓取带显式时间延迟的 RTL 语句
                r"\#[0-9]+",
                "MUST_ASSIGN_NO_DELAY and MUST_TASK_NO_TIMING_CONTROL keep delay "
                "controls out of RTL source.",
            ),
            (  # 抓取 display/finish/stop 这类仿真系统任务
                r"\$(display|finish|stop)\b",
                "MUST_TASK_NO_TIMING_CONTROL keeps simulation system tasks out of "
                "RTL source.",
            ),
        ):

            # 命中当前模式时需要登记错误。
            if re.search(str_pattern, str_line):

                # 记录当前仿真专用结构问题。
                list_issues.append(
                    StaticLintIssue(
                        "error",
                        str_message,
                        str_rel_path,
                        int_index,
                        code="SIM_ONLY",
                    )
                )

    # 把仿真专用结构诊断交回主扫描流程。
    return list_issues

# 从端口和网表声明中提取每个标识符的声明位宽。
def _declared_widths(list_lines: list[str]) -> dict[str, int]:
    """
    返回标识符到声明位宽的映射表。

    参数:
        list_lines: 已去掉注释后的源码行列表。

    返回:
        以标识符名为 key、以推断位宽为 value 的字典。
    """

    # 汇总当前文件中各标识符的声明位宽。
    dict_widths: dict[str, int] = {}  # 标识符到声明位宽的映射表

    # 先把普通声明模式拆成两段，避免一行里塞入过长的正则。
    str_plain_decl_pattern = (
        r"\b(?:input|output|inout|wire|reg)\b"  # 允许的声明起始关键字
        r"(?:\s+reg|\s+wire)?\s*(\[[^]]+\])?\s*([^;]+);"  # 可选类型、位宽和尾部名字片段
    )  # 普通声明匹配模式源码

    # 再把普通声明模式编译成可复用的正则对象。
    pattern_decl: re.Pattern[str] = re.compile(str_plain_decl_pattern)  # 匹配非 ANSI 风格声明中的位宽和尾部名字片段

    # 先把 ANSI 风格端口模式拆成两段，减少单行长度并保留语义解释。
    str_ansi_decl_pattern = (
        r"\b(?:input|output|inout|wire|reg)\b"  # 允许的 ANSI 端口方向或类型关键字
        r"(?:\s+reg|\s+wire)?\s*(\[[^]]+\])?\s+([A-Za-z_][A-Za-z0-9_]*)"  # 可选位宽和端口名
    )  # ANSI 风格端口匹配模式源码

    # 再把 ANSI 风格端口模式编译成可复用的正则对象。
    pattern_ansi_decl: re.Pattern[str] = re.compile(str_ansi_decl_pattern)  # 匹配 ANSI 风格端口声明中的位宽和端口名

    # 顺序扫描每一行声明文本，提取所有可判定位宽。
    for str_line in list_lines:

        # ANSI 风格端口声明可能在同一行出现多次，需要逐个命中。
        for match_ansi in pattern_ansi_decl.finditer(str_line):

            # 把 ANSI 风格端口名登记到位宽表。
            dict_widths[match_ansi.group(2)] = _range_width(match_ansi.group(1))  # 记录 ANSI 端口名对应的声明位宽

        # 再尝试匹配普通声明语句。
        match_decl: re.Match[str] | None = pattern_decl.search(str_line)  # 尝试抽取普通声明中的位宽与名字片段

        # 当前行不是普通声明语句时无需继续处理。
        if not match_decl:

            # 没有尾分号声明结构的源码行不会补充位宽样本。
            continue

        # 先把当前声明行的位宽范围统一解析成整数。
        int_width = _range_width(match_decl.group(1))  # 当前声明行对应的位宽值

        # 逗号分隔的声明行需要逐个提取标识符名。
        for str_raw_name in match_decl.group(2).split(","):

            # 位宽声明尾部可能混有方向或数组残片，这里只抽真实信号名。
            match_name: re.Match[str] | None = re.search(r"([A-Za-z_][A-Za-z0-9_]*)", str_raw_name)  # 从声明尾部片段中抽取真实标识符名

            # 命中标识符名时才登记到位宽表。
            if match_name:

                # 把当前标识符及其位宽登记到结果表。
                dict_widths[match_name.group(1)] = int_width  # 记录该标识符对应的声明位宽

    # 把声明位宽映射表交回位宽规则复用。
    return dict_widths

# 从 parameter 和 localparam 声明中提取常量名集合。
def _constant_names(list_lines: list[str]) -> set[str]:
    """
    返回当前文件中被视为常量的名字集合。

    参数:
        list_lines: 已去掉注释后的源码行列表。

    返回:
        按声明抽取得到的 parameter/localparam 名字集合。
    """

    # 汇总 parameter 和 localparam 名字。
    set_names: set[str] = set()  # 这里汇总 parameter 与 localparam 暴露出来的常量名

    # 先准备 parameter/localparam 声明匹配式，便于在循环里重复复用。
    str_parameter_decl_pattern = r"\b(?:parameter|localparam)\b(?:\s+\[[^]]+\])?\s+([^;]+);"  # parameter/localparam 名字片段提取模式

    # 顺序扫描每一行，查找 parameter/localparam 声明。
    for str_line in list_lines:

        # 先匹配当前行是否包含 parameter/localparam 声明。
        match_decl: re.Match[str] | None = re.search(str_parameter_decl_pattern, str_line)  # 提取 parameter/localparam 声明中的名字片段

        # 当前行不是参数声明时无需继续处理。
        if not match_decl:

            # 直接跳过当前非参数声明行。
            continue

        # 逗号分隔的参数声明需要逐个提取名字。
        for str_raw_name in match_decl.group(1).split(","):

            # 从当前片段里抽取真实参数名。
            match_name: re.Match[str] | None = re.search(r"([A-Za-z_][A-Za-z0-9_]*)", str_raw_name)  # 从当前参数片段中抽取名字

            # 命中参数名时才把它加入常量集合。
            if match_name:

                # 把当前参数名登记到常量集合。
                set_names.add(match_name.group(1))

    # 把常量名集合交回 for 边界规则复用。
    return set_names

# 把位宽范围文本转换成整数位宽。
def _range_width(str_range_text: str | None) -> int:
    """
    把形如 `[7:0]` 的范围文本转换成整数位宽。

    参数:
        str_range_text: 位宽范围文本；缺失时按单比特处理。

    返回:
        解析得到的整数位宽。
    """

    # 缺少范围文本时按单比特宽度处理。
    if not str_range_text:

        # 返回 1，表示当前声明是单比特宽度。
        return 1

    # 先匹配位宽范围里的左右端点。
    match_range: re.Match[str] | None = re.search(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", str_range_text)  # 匹配形如 [msb:lsb] 的简单整数范围

    # 不符合简单整数范围格式时同样回退到单比特。
    if not match_range:

        # 返回 1，表示当前范围无法精确解析。
        return 1

    # 提取位宽范围的左右端点整数值。
    int_left = int(match_range.group(1))  # 位宽范围左端点

    # 再读取右端点，后面会和左端点共同计算真实位宽。
    int_right = int(match_range.group(2))  # 位宽范围右端点

    # 返回左右端点差值对应的真实位宽。
    return abs(int_left - int_right) + 1

# 推断简单标识符或字面量表达式的位宽。
def _expr_width(
    str_expr: str,
    dict_widths: dict[str, int],
) -> int | None:
    """
    返回简单表达式的可判定位宽。

    参数:
        str_expr: 需要推断位宽的简单表达式文本。
        dict_widths: 当前文件中各标识符的声明位宽表。

    返回:
        能判定时返回整数位宽，无法判定时返回 None。
    """

    # 先统一去掉表达式首尾空白。
    str_expr = str_expr.strip()  # 先清理位宽待判定表达式两端的无关空白

    # 先判断当前表达式是否是显式位宽字面量。
    match_literal: re.Match[str] | None = re.fullmatch(r"(\d+)\s*'\s*[bBoOdDhH][0-9a-fA-F_xXzZ]+", str_expr)  # 判断是否命中显式位宽字面量

    # 字面量命中时直接返回其显式位宽。
    if match_literal:

        # 返回字面量前缀里声明的位宽。
        return int(match_literal.group(1))

    # 再判断当前表达式是否是单个标识符。
    match_identifier: re.Match[str] | None = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)", str_expr)  # 判断是否命中单个标识符

    # 标识符命中时从声明位宽表里查找其位宽。
    if match_identifier:

        # 返回当前标识符在位宽表中的声明位宽。
        return dict_widths.get(match_identifier.group(1))

    # 复杂表达式无法在当前阶段稳定推断位宽。
    return None

# 判断表达式是否属于可接受的常量边界形式。
def _is_constant_expr(
    str_expr: str,
    set_constants: set[str],
) -> bool:
    """
    判断表达式是否可视为常量边界。

    参数:
        str_expr: 需要判断的表达式文本。
        set_constants: 当前文件中被视为常量的名字集合。

    返回:
        表达式属于整数、显式位宽字面量或已知常量名时返回 True。
    """

    # 先裁掉循环边界文本两端的空白，避免无关字符影响常量判断。
    str_expr = str_expr.strip()  # 先清理循环边界表达式外围空白再做常量识别

    # 当前表达式命中任何一种常量形式时都视为真。
    return bool(
        re.fullmatch(r"\d+", str_expr)
        or re.fullmatch(r"\d+\s*'\s*[bBoOdDhH][0-9a-fA-F_]+", str_expr)
        or str_expr in set_constants
    )

# 判断一行文本里是否存在真正的阻塞赋值。
def _has_blocking_assignment(str_line: str) -> bool:
    """
    判断一行文本里是否包含真正的阻塞赋值。

    参数:
        str_line: 需要判断的单行源码文本。

    返回:
        命中阻塞赋值时返回 True，否则返回 False。
    """

    # 先移除比较运算符，避免把它们误判成赋值。
    str_normalized_line = re.sub(r"(==|!=|<=|>=)", "", str_line)  # 去掉比较运算符后再检测裸等号

    # 返回当前行是否仍包含单独的阻塞赋值号。
    return bool(re.search(r"(?<![<>=!])=(?!=)", str_normalized_line))

# 把源码按 always 头切分成若干块，供赋值风格规则复用。
def _always_blocks(list_lines: list[str]) -> list[tuple[int, list[str]]]:
    """
    返回源码中的 always 块列表。

    参数:
        list_lines: 已去掉注释后的源码行列表。

    返回:
        每个元素都是 `(起始行号, 块内源码行列表)` 形式的元组。
    """

    # 汇总当前文件中所有 always 块。
    list_blocks: list[tuple[int, list[str]]] = []  # always 块起始行与源码列表的集合

    # 使用索引指针顺序扫描整份源码。
    int_index = 0  # 当前扫描到的源码行下标

    # 只要还没有扫描到文件末尾，就持续提取 always 块。
    while int_index < len(list_lines):

        # 当前扫描位置对应的源码行文本。
        str_line = list_lines[int_index]  # 当前索引位置的源码行

        # 非 always 头行不需要开启新的块提取流程。
        if not re.search(r"\balways\s*@", str_line):

            # 指针向后推进一行，继续扫描。
            int_index += 1  # 越过当前非 always 行，继续寻找下一个块头

            # 普通语句不会开启新的 always 块，扫描指针直接后移。
            continue

        # 起始行号采用 1 基计数，供问题报告直接引用。
        int_start_line = int_index + 1  # 当前 always 块的 1 基起始行号

        # 当前块先把头部语句纳入结果列表。
        list_block_lines = [str_line]  # 当前 always 块已收集的源码行列表

        # begin/end 深度从头部语句开始累计。
        int_depth = _begin_delta(str_line)  # 当前 always 块的 begin/end 嵌套深度

        # 头部行已经消费，指针先推进到下一行。
        int_index += 1  # 头行已消费，扫描位置前移到块体首行

        # 继续向后吸收当前 always 块剩余的源码行。
        while int_index < len(list_lines):

            # 内层扫描准备把下一条块体语句吸收进当前 always 片段。
            str_current_line = list_lines[int_index]  # 当前块内待吸收的源码行

            # 先把当前行并入块内容。
            list_block_lines.append(str_current_line)

            # 根据当前行更新 begin/end 嵌套深度。
            int_depth += _begin_delta(str_current_line)  # 同步累计当前块体的 begin/end 深度

            # 当前行已经纳入块内，指针继续向后推进。
            int_index += 1  # 当前块内行已消费，扫描位置继续向后推进

            # begin/end 深度归零且当前行为 end 时，块已经闭合。
            if int_depth <= 0 and re.search(r"\bend\b", str_current_line):

                # 当前 always 块已完整闭合，结束内层提取。
                break

            # 无 begin 的单行 always 在分号闭合后即可结束。
            if (
                int_depth == 0
                and ";" in str_current_line
                and not re.search(r"\bbegin\b", list_block_lines[0])
            ):

                # 当前单行 always 块已经结束。
                break

        # 把当前完整 always 块登记到结果列表。
        list_blocks.append((int_start_line, list_block_lines))

    # 把切分完成的 always 块列表交回上层规则复用。
    return list_blocks

# 计算单行文本对 begin/end 嵌套深度的净增量。
def _begin_delta(str_line: str) -> int:
    """
    返回单行文本对 begin/end 深度的净增量。

    参数:
        str_line: 需要统计 begin/end 的单行源码文本。

    返回:
        当前行带来的 begin/end 深度净增量。
    """

    # 统计当前行中 begin 关键字出现次数。
    int_begin_count = len(re.findall(r"\bbegin\b", str_line))  # 当前行里的 begin 数量

    # 这里先把裸 end 个数算出来，后面还要扣掉 endcase 等复合关键字。
    int_end_count = len(re.findall(r"\bend\b", str_line))  # 粗略统计当前行里出现的 end 关键字数量

    # `endcase` 等复合关键字不应算作块级 end。
    int_end_count -= len(re.findall(r"\bend(case|module|generate|function|task)\b", str_line))  # 扣掉不代表块结束的复合 end 关键字

    # 返回当前行对 begin/end 深度的净变化量。
    return int_begin_count - max(int_end_count, 0)

# 从规格字典中提取可以信赖的时钟名集合。
def _clock_names(dict_spec: dict[str, Any]) -> set[str]:
    """
    返回规格里确认存在的时钟名称集合。

    参数:
        dict_spec: 当前任务的规格字典。

    返回:
        从 clock 字段和接口端口中收集到的时钟名集合。
    """

    # 汇总规格里显式声明或可推断的时钟名。
    set_names: set[str] = set()  # 这里汇总规格里被认可为真实时钟的名字

    # 先读取顶层 `clock` 字段，只有字典形式才可信。
    dict_clock_spec = dict_spec.get("clock") if isinstance(dict_spec.get("clock"), dict) else {}  # 顶层 clock 字段在可信时提取成字典对象

    # 顶层时钟字典显式给出名字时优先加入集合。
    if dict_clock_spec.get("name"):

        # 把顶层时钟名字加入结果集合。
        set_names.add(str(dict_clock_spec["name"]))

    # 再从接口端口定义里补充具备 clock 语义的端口。
    for dict_port in dict_spec.get("interfaces", {}).get("ports", []) or []:

        # 只有具备 name 字段的字典端口才值得继续分析。
        if not isinstance(dict_port, dict) or not dict_port.get("name"):

            # 当前端口结构不足以判定时钟语义。
            continue

        # 统一提取端口名，供 role 和命名规则共同判断。
        str_port_name = str(dict_port["name"])  # 当前端口的名称字符串

        # 统一提取端口角色文本并转成小写。
        str_role_text = str(dict_port.get("role") or "").lower()  # 当前端口的小写角色文本

        # 明确标为 clock 或名字本身像时钟时都应加入集合。
        if (
            str_role_text == "clock"
            or "clk" in str_port_name.lower()
            or "clock" in str_port_name.lower()
        ):

            # 把当前端口名加入时钟集合。
            set_names.add(str_port_name)

    # 把规格确认过的时钟名集合交回时钟相关规则复用。
    return set_names

# 去掉整段文本中的块注释，供后续逐行规则复用。
def _strip_block_comments(str_text: str) -> str:
    """
    去掉整段文本中的块注释。

    参数:
        str_text: 需要去掉块注释的完整源码文本。

    返回:
        剥离块注释后的源码文本。
    """

    # 返回去掉 `/* ... */` 块注释后的源码文本。
    return re.sub(r"/\*.*?\*/", "", str_text, flags=re.DOTALL)

# 去掉单行文本中的 `//` 行尾注释。
def _strip_line_comments(str_line: str) -> str:
    """
    去掉单行文本中的 `//` 行尾注释。

    参数:
        str_line: 需要处理的单行源码文本。

    返回:
        从 `//` 起截断后的有效源码文本。
    """

    # 返回 `//` 之前的有效源码片段。
    return str_line.split("//", 1)[0]

# 判断当前文件路径是否对应 testbench 文件。
def _is_testbench(path_source: Path) -> bool:
    """
    判断当前路径是否属于 testbench 文件。

    参数:
        path_source: 需要判断的 Verilog 文件路径。

    返回:
        文件名满足常见 testbench 命名时返回 True，否则返回 False。
    """

    # 先把文件 stem 统一转成小写，便于做命名规则判断。
    str_stem_name = path_source.stem.lower()  # 当前文件的小写 stem 名称

    # 返回当前文件是否命中 testbench 常见命名模式。
    return (
        str_stem_name.endswith("_tb")
        or str_stem_name.startswith("tb_")
        or "testbench" in str_stem_name
    )
