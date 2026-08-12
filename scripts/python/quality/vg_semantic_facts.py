"""从 formatter AST 可信范围构建 RTL VG 分析事实。"""

# future annotations 避免运行期求值递归类型。
from __future__ import annotations

# dataclass 用于声明不可变的扫描事实。
from dataclasses import dataclass

# Path 负责扫描根和 RTL 文件路径处理。
from pathlib import Path

# Any 与 Iterator 描述 AST 字典和 module 迭代器。
from typing import Any, Iterator

# formatter_ast 提供唯一受信任的 Verilog 解析入口。
from .formatter_ast import build_ast_report_for_path, iter_verilog_sources, read_verilog_source

# VgSourceFacts 保存单个文件的原文与 formatter AST。
@dataclass(frozen=True)
class VgSourceFacts:
    """保存单个 Verilog 文件的 formatter AST 与原始文本。"""

    # path 指向当前运行时可见的 RTL 文件。
    path: Path  # 当前 RTL 文件的绝对或解析后路径

    # relative_path 用于生成稳定且可迁移的报告位置。
    relative_path: str  # 相对扫描根的报告路径

    # source 保留 formatter 实际消费的完整源码。
    source: str  # 当前 RTL 文件的原始文本

    # lines 支持可信 AST span 回切源码片段。
    lines: tuple[str, ...]  # 按原始顺序保存的源码行

    # report 是 formatter AST 的结构化事实报告。
    report: dict[str, Any]  # 当前文件的 formatter AST 报告

# VgFacts 汇总一次 VG 运行的全部输入事实。
@dataclass(frozen=True)
class VgFacts:
    """保存一次 VG 门禁运行的全部可信解析事实。"""

    # root 是本轮扫描采用的解析后根路径。
    root: Path  # 本轮 VG 扫描根路径

    # sources 只包含按 testbench 策略纳入的 RTL 文件。
    sources: tuple[VgSourceFacts, ...]  # 本轮纳入分析的文件事实

    # parse_errors 汇总 formatter AST 的阻断诊断。
    parse_errors: tuple[dict[str, Any], ...]  # 无法安全分析的解析错误

    # spec 保留调用方提供的归一化设计合同。
    spec: dict[str, Any]  # 可供接口和时钟规则消费的规格

    # external_modules 只供需要跨模块接口的规则消费，不进入普通 RTL 规则扫描。
    external_modules: tuple[dict[str, Any], ...] = ()  # 受治理 stub 提供的模块接口

# build_vg_facts 只通过 formatter AST 建立规则事实。
def build_vg_facts(
    root: Path,
    *,
    spec: dict[str, Any] | None = None,
    include_testbench: bool = False,
    external_interface_sources: tuple[Path, ...] = (),
) -> VgFacts:
    """建立可供固定 VG 规则消费的可信源码事实。

    参数:
        root: 待扫描的 Verilog 文件或目录。
        spec: 可选的归一化设计规格。
        include_testbench: 是否把 testbench 纳入设计 RTL 检查。
        external_interface_sources: 只为跨模块接口规则提供事实的 stub 来源。
    返回:
        包含源码、AST、解析错误和规格的不可变事实对象。
    """

    # 解析扫描根，避免后续报告路径随调用位置漂移。
    path_root = root.resolve()  # 本轮 VG 扫描采用的规范根路径

    # 文件事实按 formatter_ast 的稳定遍历顺序收集。
    list_sources: list[VgSourceFacts] = []  # 等待规则消费的有序文件集合

    # 解析错误独立汇总，供全部 active 门禁统一 fail-closed。
    list_parse_errors: list[dict[str, Any]] = []  # formatter AST 阻断诊断

    # 逐个文件构建 formatter AST，禁止引入第二套解析器。
    for path_source in iter_verilog_sources(path_root):

        # 相对路径用于跨机器稳定定位证据。
        str_relative_path = _relative_path(path_root, path_source)  # 当前 RTL 的报告相对路径

        # 默认排除 testbench，避免仿真构造污染设计 RTL 门禁。
        if not include_testbench and _is_testbench_path(path_source):

            # 当前文件属于 testbench，继续检查下一个来源。
            continue

        # 读取器与 formatter_ast 共用编码和换行策略。
        str_source, _ = read_verilog_source(path_source)  # 当前 RTL 的规范化输入文本

        # AST 报告是后续所有结构判断的唯一解析事实。
        dict_report = build_ast_report_for_path(path_source)  # 当前 RTL 的 formatter AST 报告

        # diagnostics 可能同时包含提示和阻断错误。
        list_diagnostics = list(dict_report.get("diagnostics", []))  # 当前文件的 formatter 诊断

        # 只把 error 级诊断提升为 VG fail-closed 原因。
        for dict_diagnostic in list_diagnostics:

            # 非 error 诊断不破坏 AST 可信边界。
            if dict_diagnostic.get("severity") != "error":

                # 当前诊断无需阻断规则执行。
                continue

            # 错误诊断补上稳定相对路径后进入汇总。
            list_parse_errors.append({"path": str_relative_path, **dict_diagnostic})

        # 文件事实同时保留原文行和结构报告。
        list_sources.append(

            # 单文件对象绑定路径、原文和对应 AST，避免跨文件串扰。
            VgSourceFacts(
                path=path_source,  # 当前 RTL 的解析后文件路径
                relative_path=str_relative_path,  # 跨机器稳定的相对路径

                # 原文与行序共同支持可信 span 回切。
                source=str_source,  # formatter 实际消费的源码
                lines=tuple(str_source.splitlines()),  # 支持 AST span 回切的源码行
                report=dict_report,  # 唯一可信的 formatter AST 报告
            )
        )

    # 不可变事实对象防止规则之间相互污染输入。
    return VgFacts(
        root=path_root,  # 本轮扫描的规范根路径
        sources=tuple(list_sources),  # 稳定顺序的文件事实
        parse_errors=tuple(list_parse_errors),  # 所有阻断解析诊断
        spec=dict(spec or {}),  # 复制可选规格，隔离调用方后续修改
        external_modules=_load_external_modules(external_interface_sources),
    )

# build_vg_facts_from_reports 复用统一质量门的 AST，避免二次解析。
def build_vg_facts_from_reports(
    root: Path,
    reports: list[dict[str, Any]],
    *,
    spec: dict[str, Any] | None = None,
    external_interface_sources: tuple[Path, ...] = (),
) -> VgFacts:
    """复用统一质量门已经生成的 formatter AST 报告构建语义事实。

    参数:
        root: 本轮统一质量门的源文件或目录入口。
        reports: 已完成 formatter 解析的逐文件 AST 报告。
        spec: 可选归一化设计规格。
        external_interface_sources: 只为跨模块接口规则提供事实的 stub 来源。
    返回:
        可供全部 VG 语义规则共享的不可变事实对象。
    """

    # 解析扫描根以保持报告路径稳定。
    path_root = root.resolve()  # 统一质量门已经规范化的扫描根

    # sources 按调用方报告顺序收集文件事实。
    list_sources: list[VgSourceFacts] = []  # 复用 AST 的逐文件事实

    # parse_errors 汇总会破坏语义判断的 formatter 错误。
    list_parse_errors: list[dict[str, Any]] = []  # 复用报告中的阻断诊断

    # 每份 AST 报告与真实源文件重新绑定，但不重复执行 formatter 解析。
    for dict_report in reports:

        # 报告 path 字段恢复为当前机器可读的文件路径。
        path_source = Path(str(dict_report["path"]))  # AST 报告对应的真实源文件

        # 读取统一质量门已经解析的同一份源码。
        str_source, _ = read_verilog_source(path_source)  # 源码文本与未使用的编码标记

        # 优先沿用报告已记录的跨机器相对路径。
        str_relative_path = str(  # 报告优先提供跨机器稳定的相对路径
            dict_report.get("relative_path") or _relative_path(path_root, path_source)  # 稳定报告位置
        )

        # formatter error 必须进入语义引擎的 fail-closed 输入。
        for dict_diagnostic in dict_report.get("diagnostics", []):

            # warning 不破坏 AST 可信边界，仅收集 error。
            if dict_diagnostic.get("severity") == "error":

                # 保留文件位置和 formatter 原始诊断字段。
                list_parse_errors.append({"path": str_relative_path, **dict_diagnostic})

        # 单文件事实绑定源码、行序和调用方提供的唯一 AST 报告。
        list_sources.append(
            VgSourceFacts(
                path=path_source,  # 当前 RTL 的真实文件路径
                relative_path=str_relative_path,  # 报告使用的稳定相对路径

                # 源码文本和行序共同支持后续 span 回切。
                source=str_source,  # formatter 已消费的当前源码
                lines=tuple(str_source.splitlines()),  # AST span 回切所需行序

                # 报告对象保持调用方生成的 AST 身份。
                report=dict_report,  # 统一质量门生成的唯一 AST 报告
            )
        )

    # 聚合结果保留报告顺序，并隔离后续规则对输入集合的修改。
    return VgFacts(
        root=path_root,  # 复用报告对应的统一扫描入口
        sources=tuple(list_sources),  # 按既有 AST 报告顺序冻结文件事实
        parse_errors=tuple(list_parse_errors),  # 复用报告内的 formatter 错误集合
        spec=dict(spec or {}),  # 为语义规则复制可选设计合同
        external_modules=_load_external_modules(external_interface_sources),  # VG097 外部接口集合
    )

# 外部接口装载与普通 RTL 事实隔离，避免 stub 触发无关规则。
def _load_external_modules(tuple_sources: tuple[Path, ...]) -> tuple[dict[str, Any], ...]:
    """从显式 stub 来源读取 formatter 已确认的模块接口。

    参数:
        tuple_sources: 调用方明确提供的 Verilog stub 文件或目录。
    返回:
        仅含模块声明事实的不可变集合。
    异常:
        ValueError: stub 解析失败、模块名为空或来源中存在重复 module。
    """

    # 外部接口按调用顺序装载，重复名称交由消费规则 fail-closed。
    list_modules: list[dict[str, Any]] = []  # 外部模块接口事实

    # 已见名称阻止多个 stub 来源用遍历顺序互相覆盖。
    set_module_names: set[str] = set()  # 外部接口中已经登记的 module 名称

    # 每个显式来源独立通过 formatter AST，禁止宽松文本解析。
    for path_source_root in tuple_sources:

        # 目录和单文件都复用 formatter 的稳定 Verilog 发现顺序。
        for path_source in iter_verilog_sources(path_source_root.resolve()):

            # stub 必须通过同一 formatter AST，禁止引入宽松的第二解析器。
            dict_report = build_ast_report_for_path(path_source)  # 外部 stub AST 报告

            # 单文件 helper 集中处理解析错误、重名检查和模块登记。
            _append_external_report_modules(
                dict_report,
                path_source,
                list_modules,
                set_module_names,
            )

    # 返回稳定顺序的外部模块接口。
    return tuple(list_modules)

# 单文件 stub 报告必须在登记前通过解析和名称唯一性检查。
def _append_external_report_modules(
    dict_report: dict[str, Any],
    path_source: Path,
    list_modules: list[dict[str, Any]],
    set_module_names: set[str],
) -> None:
    """把一份可信 stub 报告追加到外部接口集合。

    参数:
        dict_report: formatter AST 生成的单文件报告。
        path_source: 报告对应的 stub 来源路径。
        list_modules: 等待追加的外部模块接口集合。
        set_module_names: 已登记的外部 module 名称。
    返回:
        本函数原地更新集合，不返回业务值。
    异常:
        ValueError: 报告含解析错误、空模块名或重复模块名。
    """

    # formatter 错误使整个 stub 来源不可作为可信接口。
    bool_has_error = any(  # 当前 stub 是否包含阻断解析诊断
        dict_item.get("severity") == "error"  # error 级诊断会破坏接口可信度
        for dict_item in dict_report.get("diagnostics", [])  # 当前 stub 的 formatter 诊断集合
    )  # 阻断当前 stub 登记的解析状态

    # 无法确认端口声明的 stub 必须阻断，不能静默退化为缺失接口。
    if bool_has_error:

        # 异常消息明确指出出错来源，供 CLI 转换为非零退出。
        raise ValueError(
            f"> ERR: [Python] External interface stub failed formatter parsing: {path_source}"
        )

    # 逐 module 检查重复名称后再加入隔离接口集合。
    for dict_module in dict_report.get("modules", []):

        # 空名称不具备可引用的接口身份。
        str_module_name = str(dict_module.get("name") or "")  # 当前外部 module 名称

        # 任意重复名称都使 stub 来源优先级不唯一。
        if not str_module_name or str_module_name in set_module_names:

            # fail-closed 阻止同名声明依赖输入顺序覆盖。
            raise ValueError(
                f"> ERR: [Python] Duplicate external interface module: {str_module_name or '<empty>'}"
            )

        # 名称确认唯一后登记，供后续来源冲突检查。
        set_module_names.add(str_module_name)

        # 只复制 module 接口事实，不把 stub 源码交给其他 VG 规则。
        list_modules.append(dict_module)

# iter_trusted_modules 只产出 formatter 已确认的 module span。
def iter_trusted_modules(facts: VgFacts) -> Iterator[tuple[VgSourceFacts, dict[str, Any], str, int]]:
    """逐个返回 formatter AST 已确认边界的 module 文本。

    参数:
        facts: 本轮 VG 扫描的不可变事实。
    返回:
        迭代产生文件事实、module 字典、可信文本和一基起始行。
    """

    # 文件顺序沿用 formatter_ast 的稳定遍历结果。
    for source_facts in facts.sources:

        # module 列表完全来自 formatter AST 报告。
        for dict_module in source_facts.report.get("modules", []):

            # 起始行用于从原文切回可信 module 文本。
            int_start = int(dict_module.get("line_start") or 0)  # module 的一基起始行

            # 结束行限定所有词法扫描的最大范围。
            int_end = int(dict_module.get("line_end") or 0)  # module 的一基结束行

            # span 缺失时不能安全执行局部词法检查。
            if int_start <= 0 or int_end < int_start:

                # 当前 module 缺少可信边界，继续处理下一项。
                continue

            # 词法规则只能消费 formatter 已确认的 module 片段。
            str_module_text = "\n".join(source_facts.lines[int_start - 1 : int_end])  # 当前可信 module 文本

            # 逐项交给规则模块，保持 AST 字典和原文 span 对齐。
            yield source_facts, dict_module, str_module_text, int_start

# source_line 为证据报告读取安全的一基源码行。
def source_line(source_facts: VgSourceFacts, int_line: int) -> str:
    """按一基行号返回源码行，越界时返回空文本。

    参数:
        source_facts: 当前 RTL 文件事实。
        int_line: 需要读取的一基源码行号。
    返回:
        对应源码行；越界时返回空字符串。
    """

    # 只有有效一基行号才能读取原文。
    if 1 <= int_line <= len(source_facts.lines):

        # 返回保持原始缩进的源码行。
        return source_facts.lines[int_line - 1]

    # 越界行号不应制造伪证据。
    return ""

# first_matching_line 把可信片段内偏移换算为文件行号。
def first_matching_line(str_text: str, str_pattern: str, int_base_line: int) -> int:
    """返回正则在可信文本中的首个一基文件行号。

    参数:
        str_text: formatter 已确认边界的源码片段。
        str_pattern: 需要定位的正则表达式。
        int_base_line: 片段在文件中的一基起始行。
    返回:
        首次命中的一基文件行；没有命中时返回起始行。
    """

    # re 只服务可信文本内的证据定位。
    import re

    # 首次匹配足以定位调用方已经确认的规则证据。
    obj_match = re.search(str_pattern, str_text, flags=re.MULTILINE | re.IGNORECASE)  # 首个可信文本匹配

    # 没有命中时保守返回片段起始行。
    if obj_match is None:

        # 起始行比伪造精确位置更安全。
        return int_base_line

    # 匹配前换行数换算为文件一基行号。
    return int_base_line + str_text.count("\n", 0, obj_match.start())

# _relative_path 统一单文件和目录扫描的报告位置。
def _relative_path(path_root: Path, path_source: Path) -> str:
    """生成稳定的相对路径。

    参数:
        path_root: 本轮扫描根路径。
        path_source: 当前 RTL 文件路径。
    返回:
        单文件扫描时返回文件名，目录扫描时返回 POSIX 相对路径。
    """

    # 单文件扫描没有可继续下钻的目录根。
    if path_root.is_file():

        # 文件名在不同机器上保持稳定。
        return path_source.name

    # 目录扫描保留层级并统一路径分隔符。
    return path_source.relative_to(path_root).as_posix()

# _is_testbench_path 根据稳定命名约定识别仿真文件。
def _is_testbench_path(path_source: Path) -> bool:
    """根据文件名和目录名识别 testbench 文件。

    参数:
        path_source: 待分类的 Verilog 文件路径。
    返回:
        路径属于 testbench 时返回 True。
    """

    # 小写 POSIX 路径统一 Windows 与 Linux 判断行为。
    str_normalized = path_source.as_posix().lower()  # 当前文件的规范化分类路径

    # 路径组件判断避免把平台分隔符编码进 testbench 目录规则。
    set_path_parts = {str_part.lower() for str_part in path_source.parts}  # 当前文件的小写路径组件

    # 同时兼容 tb 目录、testbench 名称和 _tb 文件后缀。
    return "tb" in set_path_parts or "testbench" in str_normalized or path_source.stem.lower().endswith("_tb")
