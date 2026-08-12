"""提供基于随包 formatter 后端的 Verilog 结构化 AST 报告。"""

# future annotations 避免运行期解析复杂类型提示。
from __future__ import annotations

# dataclasses 工具用于把 formatter 内部模型安全转成字典。
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

# formatter backend 在唯一解析所有者内部生成类型化组合锥事实。
from .formatter_backend.expression_facts import attach_expression_facts

# formatter 配置工厂提供唯一受控的 parser/backend 入口。
from .formatter_backend import FormatterBackend, VerilogFormatterError
from .formatter_ast_instances import (
    instance_from_control_statement as _instance_from_control_statement,
    instances_from_control_nodes as _instances_from_control_nodes,
)
from .formatter_config import create_formatter_backend

# VerilogAstError 表示 formatter AST 桥接层无法产出可信结构。
class VerilogAstError(ValueError):
    """表示 formatter parser 无法构造安全的结构化模型。"""

# 支持的后缀集合限定 AST 扫描范围。
VERILOG_EXTENSIONS = {".v"}  # Verilog-2001 源文件后缀集合

# 读取顺序优先覆盖 UTF-8，再兼容中文工程中常见编码。
SOURCE_ENCODINGS = ("utf-8", "gb18030", "latin1")  # Verilog 文本候选编码顺序

# iter_verilog_sources 收集单文件或目录树中的 RTL 输入。
def iter_verilog_sources(root: Path) -> list[Path]:
    """返回文件或目录路径下的 Verilog 源文件列表。

    参数:
        root: 待扫描的单文件或目录路径。
    返回:
        按稳定顺序排列的 Verilog 源文件路径列表。
    """

    # resolve 让后续报告使用稳定的绝对路径。
    path_resolved = root.resolve()  # 规范化后的输入路径

    # 单文件输入只在后缀匹配时进入 AST 检查。
    if path_resolved.is_file():

        # 后缀不匹配时返回空列表，避免误读非 RTL 文件。
        return [path_resolved] if path_resolved.suffix.lower() in VERILOG_EXTENSIONS else []

    # 不存在的目录按空集合处理，交给上层决定是否报错。
    if not path_resolved.exists():

        # 空目录语义保持历史兼容。
        return []

    # 目录扫描保持稳定排序，保证质量报告可复现。
    return sorted(
        path_source
        for path_source in path_resolved.rglob("*")
        if path_source.is_file() and path_source.suffix.lower() in VERILOG_EXTENSIONS
    )

# read_verilog_source 按受控编码顺序读取 RTL 文本。
def read_verilog_source(path: Path) -> tuple[str, str]:
    """按项目兼容编码读取 Verilog 文本。

    参数:
        path: 待读取的 Verilog 文件路径。
    返回:
        源文本和实际命中的编码名称。
    异常:
        VerilogAstError: 文件无法用受支持编码解码时抛出。
    """

    # 原始字节只读取一次，避免多编码回退重复访问文件系统。
    bytes_source = path.read_bytes()  # Verilog 文件原始字节

    # 解码异常列表用于最终串联最接近失败现场的根因。
    list_decode_errors: list[UnicodeDecodeError] = []  # 各候选编码失败时产生的异常集合

    # 候选编码按优先级尝试。
    for str_encoding in SOURCE_ENCODINGS:

        # 每次尝试只捕获编码错误，不吞掉 I/O 错误。
        try:

            # 成功后返回文本和实际编码。
            return bytes_source.decode(str_encoding), str_encoding

        # 仅编码失败进入下一候选编码。
        except UnicodeDecodeError as exc:

            # 保留最后一次失败，便于最终错误报告包含真实编码异常。
            list_decode_errors.append(exc)

    # 所有编码都失败时给调用方明确的文件级错误。
    if list_decode_errors:

        # 最后一条解码异常作为 VerilogAstError 的错误链根因。
        raise VerilogAstError(
            f"> ERR: [Python] unable to decode Verilog source {path}: {list_decode_errors[-1]}"
        ) from list_decode_errors[-1]

    # 空文件理论上不会进入这里，但保留兜底返回值。
    return "", "utf-8"

# build_ast_report_for_path 为单个 RTL 文件生成结构报告。
def build_ast_report_for_path(path: Path, *, profile: str = "formatter-normalize") -> dict[str, Any]:
    """为单个源文件构建结构化 AST 报告。

    参数:
        path: 待检查的 Verilog 文件路径。
        profile: formatter profile 名称或路径。
    返回:
        包含结构、诊断和编码信息的 AST 报告字典。
    """

    # 读取文本时同时保留实际编码。
    tuple_source = read_verilog_source(path)  # 单文件 AST 报告需要的文本和编码

    # 展开命名保持后续报告字段清晰。
    str_source_text = tuple_source[0]  # 传入 formatter parser 的 Verilog 源文本

    # 编码字段写入最终报告，帮助定位跨编码工程问题。
    str_encoding = tuple_source[1]  # 当前文件实际命中的读取编码

    # 文本报告复用主入口，保持路径和 profile 语义一致。
    dict_report = build_ast_report_for_text(str_source_text, source_path=path, profile=profile)  # 当前文件的 formatter AST 报告

    # encoding 字段用于诊断跨编码工程。
    dict_report["encoding"] = str_encoding  # 报告消费方展示的文件编码

    # 返回报告对象给 CLI 或 validation gate。
    return dict_report

# build_ast_report_for_tree 为目录树聚合 formatter AST 报告。
def build_ast_report_for_tree(root: Path, *, profile: str = "formatter-normalize") -> dict[str, Any]:
    """为目录树中的每个 Verilog 源文件构建 AST 报告。

    参数:
        root: 待扫描的单文件或目录根路径。
        profile: formatter profile 名称或路径。
    返回:
        聚合所有匹配源文件的 AST 目录报告。
    """

    # 文件发现逻辑集中在 iter_verilog_sources。
    list_files = iter_verilog_sources(root)  # 待检查 Verilog 文件列表

    # 单文件报告保留原始文件顺序，便于定位。
    list_file_reports: list[dict[str, Any]] = []  # 目录报告中的逐文件 AST 报告列表

    # 每个文件单独生成报告，便于失败时定位路径。
    for path_source in list_files:

        # 单文件 AST 报告追加到目录级报告。
        list_file_reports.append(build_ast_report_for_path(path_source, profile=profile))

    # 诊断列表平铺供 summary 聚合使用。
    list_diagnostics: list[dict[str, Any]] = []  # 目录级 summary 需要聚合的全部诊断

    # 逐文件展开 diagnostics 字段，避免上层重复扫描。
    for dict_report in list_file_reports:

        # 当前文件的诊断追加到目录级诊断集合。
        list_diagnostics.extend(dict_report.get("diagnostics", []))

    # 目录报告保留旧版 JSON 契约。
    return {
        "version": 1,
        "root": str(root.resolve()),
        "profile": profile,
        "ok": _tree_reports_are_ok(list_file_reports, list_diagnostics),
        "files": list_file_reports,
        "summary": _tree_summary(list_file_reports, list_diagnostics),
    }

# build_ast_report_for_text 是 formatter AST 的主解析入口。
def build_ast_report_for_text(
    source: str,
    *,
    source_path: Path | None = None,
    profile: str = "formatter-normalize",
) -> dict[str, Any]:
    """为 Verilog 文本构建 formatter 后端支撑的 AST 报告。

    参数:
        source: 待解析的 Verilog 源文本。
        source_path: 可选源路径，用于报告展示。
        profile: formatter profile 名称或路径。
    返回:
        包含 header、module、诊断、formatter 违规和文本指标的 AST 报告。
    """

    # formatter 比较必须在 AST 内部解析前执行，避免共享配置状态污染检查结果。
    formatter_backend_check_engine: FormatterBackend = create_formatter_backend(profile=profile)  # 只负责模板一致性检查的后端

    # check_text 结果保留为 formatter 原生违规文本。
    list_formatter_violations = _formatter_violations(formatter_backend_check_engine, source, source_path)  # 报告中保留的 formatter 原生违规文本

    # AST 解析使用独立后端，避免 check_text 的格式化状态进入结构报告。
    formatter_backend_formatter_engine: FormatterBackend = create_formatter_backend(profile=profile)  # 负责 AST 结构解析的后端

    # diagnostics 收集 warning/error，不让 parser 细节泄漏到调用方。
    list_diagnostics: list[dict[str, Any]] = []  # AST 诊断列表

    # formatter parser 负责提取 header 与 module 结构。
    tuple_parse = _parse_source_with_formatter(formatter_backend_formatter_engine, source, list_diagnostics)  # header 与模块解析结果

    # header 字段描述 formatter 识别出的文件头信息。
    dict_header = tuple_parse[0]  # 文件头元数据

    # modules 字段描述 formatter 识别出的 module 结构。
    list_modules = tuple_parse[1]  # module 结构列表

    # 表达式事实由 formatter 层一次构建，后续语义门禁不得重新解析源码文本。
    attach_expression_facts(list_modules)

    # parse error 与 formatter mismatch 分开计数，避免模板不一致被解析成功掩盖。
    int_parse_errors = sum(  # 当前文本的 formatter AST 解析错误数
        1  # 每条 error 诊断计为一个解析错误
        for dict_item in list_diagnostics  # 遍历 parser 返回的全部诊断
        if dict_item.get("severity") == "error"  # 只统计阻断级解析诊断
    )

    # 每条 formatter violation 都是模板一致性错误。
    int_formatter_errors = len(list_formatter_violations)  # 当前文本的 formatter 模板错误数

    # 返回字段保持 v0.3.0 quality gate 契约。
    return {
        "version": 1,
        "path": str(source_path) if source_path is not None else None,
        "profile": profile,
        "ok": int_parse_errors == 0 and int_formatter_errors == 0,
        "header": dict_header,
        "formatter_violations": list_formatter_violations,
        "diagnostics": list_diagnostics,
        "modules": list_modules,
        "source_metrics": _source_metrics(source),
        "summary": {
            "parse_errors": int_parse_errors,
            "formatter_errors": int_formatter_errors,
            "errors": int_parse_errors + int_formatter_errors,
        },
    }

# normalize_text_with_formatter_ast 对格式化结果再跑 AST 检查。
def normalize_text_with_formatter_ast(
    source: str,
    *,
    source_path: Path | None = None,
    profile: str = "formatter-normalize",
) -> tuple[str, dict[str, Any]]:
    """通过随包 formatter 规范化文本，并返回规范化后的 AST 报告。

    参数:
        source: 待格式化的 Verilog 源文本。
        source_path: 可选源路径，用于 formatter 报告展示。
        profile: formatter profile 名称或路径。
    返回:
        格式化后的源文本和二次 AST 报告。
    异常:
        VerilogAstError: formatter 拒绝格式化结果时抛出。
    """

    # 后端创建沿用同一 profile，确保 format 和 AST 检查一致。
    formatter_backend_formatter_engine: FormatterBackend = create_formatter_backend(profile=profile)  # formatter 后端实例

    # format_text 可能被写回策略拒绝，错误转换为 AST 层错误。
    try:

        # formatted 是 formatter 后端的唯一输出文本。
        str_formatted = formatter_backend_formatter_engine.format_text(source, source_path)  # 格式化后的 RTL 文本

    # formatter 层异常统一提升为 AST 层异常。
    except VerilogFormatterError as exc:

        # 调用方只需要处理 VerilogAstError。
        raise VerilogAstError(f"> ERR: [Python] formatter normalization failed: {exc}") from exc

    # 第二次格式化只用于验证首次输出已经收敛，禁止循环多次掩盖不稳定行为。
    try:

        # 使用同一后端和来源路径执行严格的单次幂等复检。
        str_reformatted = formatter_backend_formatter_engine.format_text(str_formatted, source_path)  # 第二次 formatter 输出

    # 幂等复检异常沿用 AST 层统一错误类型。
    except VerilogFormatterError as exc:

        # 调用方无需区分首次格式化与幂等复检异常。
        raise VerilogAstError(f"> ERR: [Python] formatter idempotence check failed: {exc}") from exc

    # 第二次输出变化说明 formatter 尚未在单次调用后收敛。
    if str_reformatted != str_formatted:

        # 不稳定输出不得继续进入写回或交付路径。
        raise VerilogAstError("> ERR: [Python] formatter output is not idempotent after one pass")

    # 对格式化结果重跑结构报告，确保输出仍可解析。
    dict_report = build_ast_report_for_text(str_formatted, source_path=source_path, profile=profile)  # 格式化输出的二次 AST 报告

    # 返回文本和报告给 model provider 或 CLI。
    return str_formatted, dict_report

# _parse_source_with_formatter 调用 formatter 内部结构化 parser。
def _parse_source_with_formatter(
    formatter_engine: Any,
    source: str,
    list_diagnostics: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """调用 formatter parser 提取 header 和 module 列表。

    参数:
        formatter_engine: 随包 formatter 后端实例，需提供内部 parser 钩子。
        source: 待解析的 Verilog 源文本。
        list_diagnostics: 调用方维护的 AST 诊断列表。
    返回:
        可选 header 字典和 module 字典列表。
    异常:
        本函数会捕获 formatter parser 异常并转换为诊断，不向外抛出。
    """

    # header 默认为 None，表示源文本没有可识别文件头。
    dict_header: dict[str, Any] | None = None  # 报告顶层可选 header 字段

    # module 列表为空时由 parser 诊断记录 error。
    list_modules: list[dict[str, Any]] = []  # 报告顶层 modules 字段内容

    # 复用随包 formatter parser，避免 AST 报告引入另一套 Verilog 解析器。
    try:

        # 先提取文件头，再对无头文本切 module。
        tuple_header = formatter_engine._extract_header_metadata_and_source(source)  # 文件头和正文切分结果

        # clean source 是 module section parser 的输入正文。
        str_clean_source = tuple_header[1]  # 去除文件头后用于 module 切分的 RTL 正文

        # header dataclass 安全转换为 JSON 字典。
        dict_header = _safe_dataclass_dict(tuple_header[0]) if tuple_header[0] is not None else None  # JSON 化文件头字段

        # module section 保留 formatter 的模块切分边界。
        list_module_sections = formatter_engine._split_module_sections(str_clean_source)  # module 边界切分结果

        # 没有 module 时 AST 报告必须失败。
        if not list_module_sections:

            # 明确指出 formatter parser 未发现 module 声明。
            raise VerilogAstError(
                "> ERR: [Python] no module declaration was found by the formatter parser."
            )

        # 搜索游标让重复 module 文本也能按源码顺序定位。
        int_search_offset = 0  # 下一次 module 文本查找的起始偏移

        # 每个 module section 单独解析，便于定位坏模块。
        for int_index, dict_section in enumerate(list_module_sections):

            # module_text 是 formatter section 中的原始模块文本。
            str_module_text = str(dict_section.get("module_text") or "")  # 当前 module 文本

            # 在原始源码中定位 module 文本，供后续 AST span 标注使用。
            int_module_offset = source.find(str_module_text, int_search_offset)  # 当前 module 在源文本中的字符偏移

            # 解析后的 module 字典先保存在局部变量中，便于补充行号范围。
            dict_module = _parse_module_with_formatter(formatter_engine, str_module_text, index=int_index)  # 当前 module 结构报告

            # 行号标注只基于 formatter 已切出的结构文本，不重新解析 Verilog 语义。
            _attach_module_line_spans(dict_module, source, str_module_text, int_module_offset)

            # 当前 module 结构报告追加到文件级报告。
            list_modules.append(dict_module)

            # 成功定位时推进游标，避免同名 wrapper 文本重复命中前一个模块。
            if int_module_offset >= 0:

                # 下一次查找从当前 module 末尾继续。
                int_search_offset = int_module_offset + len(str_module_text)  # 后续 module 搜索起点

    # parser 异常转换为 AST 诊断，避免 CLI 直接崩溃。
    except Exception as exc:

        # parser 失败是 hard error，质量报告不能声明 ok。
        list_diagnostics.append(_diagnostic("error", "FORMATTER_AST_PARSE", str(exc)))

    # 返回 header 和 module 列表给主报告。
    return dict_header, list_modules

# _formatter_violations 封装 check_text 的异常容错。
def _formatter_violations(
    formatter_engine: Any,
    source: str,
    source_path: Path | None,
) -> list[str]:
    """返回 formatter check_text 的原生违规文本。

    参数:
        formatter_engine: 随包 formatter 后端实例。
        source: 待检查的 Verilog 源文本。
        source_path: 可选源路径，用于 formatter 报告展示。
    返回:
        formatter check_text 输出的违规文本列表。
    """

    # check_text 本身可能因为不完整 RTL 抛错，此时保留错误文本。
    try:

        # list 化结果保证 JSON 可序列化。
        return list(formatter_engine.check_text(source, source_path))

    # check_text 失败时以文本形式保留原始异常。
    except Exception as exc:

        # 违规文本仍由调用方展示，不转换为 AST error。
        return [str(exc)]

# module 转换继续消费包含 generate 实例的统一集合。
def _parse_module_with_formatter(formatter_engine: Any, module_text: str, *, index: int) -> dict[str, Any]:
    """解析单个 module section 并返回结构化字典。

    参数:
        formatter_engine: 随包 formatter 后端实例，需提供内部 parser 钩子。
        module_text: 单个 module section 的原始文本。
        index: module 在源文件中的稳定序号。
    返回:
        兼容 formatter AST quality gate 契约的 module 结构字典。
    """

    # formatter parser 返回 module 名、头参数、端口和 body。
    tuple_module = formatter_engine._parse_module(module_text)  # module parser 原始元组

    # module 名称进入报告的 name 字段。
    str_module_name = tuple_module[0]  # 当前 module 的声明名称

    # header parameter 保留声明顺序。
    list_header_params = tuple_module[1]  # 当前 module 头部参数模型列表

    # raw ports 保留 formatter 解析出的端口模型。
    list_raw_ports = tuple_module[2]  # 当前 module 头部端口模型列表

    # body 文本继续交给 body parser。
    str_body = tuple_module[3]  # 当前 module 的 body 源文本

    # body preamble 需要单独保留，避免注释和属性块丢失。
    tuple_body = formatter_engine._extract_body_leading_preamble(str_body)  # body 前导块切分结果

    # body preamble 作为独立字段写入报告。
    list_body_preamble_blocks = tuple_body[0]  # 当前 module body 的前导块列表

    # 去除 preamble 后的文本才进入 body parser。
    str_body_without_preamble = tuple_body[1]  # 去除前导块后的 body 文本

    # formatter body parser 输出声明、assign、always 等结构集合。
    dict_body_items = formatter_engine._parse_body(str_body_without_preamble)  # body 结构分类结果

    # always 块提前转换，避免 return 字段过于拥挤。
    list_always_blocks = [_always_to_dict(item) for item in dict_body_items.get("always", [])]  # 当前 module 的 always 报告列表

    # 声明提前转换，供 decls 字段直接复用。
    list_declarations = [_signal_to_dict(item) for item in dict_body_items.get("decls", [])]  # 当前 module 的声明报告列表

    # assign 集合直接映射连续赋值报告字段。
    list_assigns = [_assign_to_dict(item) for item in dict_body_items.get("assigns", [])]  # 当前 module 的连续赋值报告列表

    # localparam 集合保留参数声明顺序。
    list_localparams = [_param_to_dict(item) for item in dict_body_items.get("localparams", [])]  # 当前 module 的局部参数报告列表

    # generate 内部的控制节点也需要向语义门禁暴露实例事实。
    list_instances = list(dict_body_items.get("instances", []))  # module 全部可见实例

    # 每个 generate 控制树按出现顺序合并内部实例。
    for obj_generate in dict_body_items.get("generates", []) or []:

        # 只对已经由 generate parser 确认的 statement 节点重用 body 实例解析。
        list_instances.extend(_instances_from_control_nodes(formatter_engine, obj_generate.nodes))

    # 返回字段沿用 formatter AST quality gate 的既有契约。
    return {
        "index": index,
        "name": str_module_name,
        "params": [_param_to_dict(item) for item in list_header_params],
        "ports": [_port_to_dict(item) for item in list_raw_ports],
        "body_preamble_blocks": [str(item) for item in list_body_preamble_blocks],
        "localparams": list_localparams,
        "decls": list_declarations,
        "assigns": list_assigns,
        "always": list_always_blocks,
        "instances": [_instance_to_dict(item) for item in list_instances],
        "generates": [_block_to_dict(item) for item in dict_body_items.get("generates", [])],
        "initials": [_block_to_dict(item) for item in dict_body_items.get("initials", [])],
        "functions": [_block_to_dict(item) for item in dict_body_items.get("functions", [])],
        "tasks": [_block_to_dict(item) for item in dict_body_items.get("tasks", [])],
        "raw_blocks": [_block_to_dict(item) for item in dict_body_items.get("raw_blocks", [])],
        "conditionals": [_block_to_dict(item) for item in dict_body_items.get("conditionals", [])],
        "counts": _body_item_counts(dict_body_items),
    }

# _body_item_counts 汇总 formatter body parser 的列表型字段数量。
def _body_item_counts(dict_body_items: dict[str, Any]) -> dict[str, int]:
    """统计 body_items 中列表型结构的数量。

    参数:
        dict_body_items: formatter body parser 输出的结构分类字典。
    返回:
        每个列表型 body 字段的元素数量。
    """

    # blocks 是 parser 内部聚合字段，不进入外部计数。
    return {
        str_key: len(value)
        for str_key, value in dict_body_items.items()
        if isinstance(value, list) and str_key != "blocks"
    }

# _param_to_dict 转换 formatter parameter 模型。
def _param_to_dict(item: Any) -> dict[str, Any]:
    """把 formatter 参数模型转换成 JSON 字典。

    参数:
        item: formatter 参数 dataclass、dict 或其他兼容对象。
    返回:
        包含 keyword、name、value 等字段的参数报告字典。
    """

    # 参数模型先标准化，后续字段读取即可兼容 dataclass 与 dict。
    dict_item = _safe_dataclass_dict(item)  # 参数字段字典

    # 返回字段保持与历史报告兼容。
    return {
        "keyword": dict_item.get("keyword", ""),
        "name": dict_item.get("name", ""),
        "value": dict_item.get("value", ""),
        "decl_spec": dict_item.get("decl_spec", ""),
        "comment": dict_item.get("comment", ""),
        "leading_comments": dict_item.get("leading_comments", []),
        "synthetic": bool(dict_item.get("synthetic", False)),
    }

# _port_to_dict 转换 formatter port 模型并保留接口分组信息。
def _port_to_dict(item: Any) -> dict[str, Any]:
    """把 formatter 端口模型转换成 JSON 字典。

    参数:
        item: formatter 端口 dataclass、dict 或其他兼容对象。
    返回:
        包含方向、位宽、名称、分组和属性的端口报告字典。
    """

    # 端口模型先标准化，保证方向、宽度和分组字段按同一路径读取。
    dict_item = _safe_dataclass_dict(item)  # 端口字段字典

    # 返回字段覆盖方向、宽度、分组和属性。
    return {
        "direction": dict_item.get("direction", ""),
        "width": dict_item.get("width", ""),
        "name": dict_item.get("name", ""),
        "comment": dict_item.get("comment", ""),
        "group": dict_item.get("group", ""),
        "section": dict_item.get("section", ""),
        "signed": bool(dict_item.get("signed", False)),
        "unpacked": dict_item.get("unpacked", ""),
        "attributes": dict_item.get("attributes", ""),
        "subgroup": dict_item.get("subgroup", ""),
        "synthetic": bool(dict_item.get("synthetic", False)),
    }

# _signal_to_dict 转换 formatter declaration 模型并保留声明属性。
def _signal_to_dict(item: Any) -> dict[str, Any]:
    """把 formatter 信号声明模型转换成 JSON 字典。

    参数:
        item: formatter 声明 dataclass、dict 或其他兼容对象。
    返回:
        包含 kind、width、name、init 等字段的声明报告字典。
    """

    # 信号声明模型先标准化，避免 dataclass 字段访问和 dict 访问混用。
    dict_item = _safe_dataclass_dict(item)  # 信号声明字段字典

    # 返回字段覆盖声明类型、位宽、初值和属性。
    return {
        "kind": dict_item.get("kind", ""),
        "width": dict_item.get("width", ""),
        "name": dict_item.get("name", ""),
        "init": dict_item.get("init", ""),
        "comment": dict_item.get("comment", ""),
        "signed": bool(dict_item.get("signed", False)),
        "unpacked": dict_item.get("unpacked", ""),
        "attributes": dict_item.get("attributes", ""),
        "suffix": dict_item.get("suffix", ""),
        "leading_comments": dict_item.get("leading_comments", []),
    }

# _assign_to_dict 转换 formatter assign 模型并保留表达式两侧。
def _assign_to_dict(item: Any) -> dict[str, Any]:
    """把 formatter assign 模型转换成 JSON 字典。

    参数:
        item: formatter 连续赋值 dataclass、dict 或其他兼容对象。
    返回:
        包含 lhs、rhs、comment、delay 等字段的赋值报告字典。
    """

    # 连续赋值模型先标准化，确保左右侧表达式和注释字段稳定导出。
    dict_item = _safe_dataclass_dict(item)  # assign 字段字典

    # 返回字段覆盖赋值左右侧、注释和延迟。
    return {
        "lhs": dict_item.get("lhs", ""),
        "rhs": dict_item.get("rhs", ""),
        "comment": dict_item.get("comment", ""),
        "leading_comments": dict_item.get("leading_comments", []),
        "delay": dict_item.get("delay", ""),
    }

# _case_item_to_dict 固定 case 分支在 AST 报告中的递归字段。
def _case_item_to_dict(item: Any) -> dict[str, Any]:
    """把 formatter case item 转换成稳定的 JSON 字典。

    参数:
        item: formatter CaseItem、dict 或兼容对象。
    返回:
        包含标签、块标签和分支子节点的字典。
    """

    # dataclass 与字典统一转换后再显式选择公共字段。
    dict_item = _safe_dataclass_dict(item)  # case item 的完整字段映射

    # 子节点递归使用同一控制节点序列化合同。
    list_children = [  # 当前 case 分支内部的控制节点
        _control_node_to_dict(dict_child)  # 递归保留子节点的稳定字段
        for dict_child in dict_item.get("children", []) or []  # 遍历 formatter 解析出的分支主体
    ]

    # 返回值不暴露 dataclass 内部实现细节。
    return {
        "label": dict_item.get("label", ""),
        "block_label": dict_item.get("block_label", ""),
        "children": list_children,
    }

# _control_node_to_dict 递归导出 formatter 控制流树。
def _control_node_to_dict(item: Any) -> dict[str, Any]:
    """把 formatter ControlNode 转换成稳定的递归字典。

    参数:
        item: formatter ControlNode、dict 或兼容对象。
    返回:
        包含节点身份、文本、主路径、备选路径和 case items 的字典。
    """

    # 先统一 dataclass 和字典输入，避免递归分支分别处理类型。
    dict_item = _safe_dataclass_dict(item)  # 当前控制节点的完整字段映射

    # 主路径节点保持 formatter 原始顺序。
    list_children = [  # 当前控制节点的主路径子树
        _control_node_to_dict(dict_child)  # 递归转换主路径子节点
        for dict_child in dict_item.get("children", []) or []  # 遍历主路径节点
    ]

    # alternate 单独保留 if/else 的互斥路径身份。
    list_alternate = [  # 当前控制节点的备选路径子树
        _control_node_to_dict(dict_child)  # 递归转换备选路径子节点
        for dict_child in dict_item.get("alternate", []) or []  # 遍历 formatter alternate 节点
    ]

    # VG076 与 VG105 通过标签文本和分支子树判断 case 路径违规。
    list_items = [
        _case_item_to_dict(dict_case_item)  # 保留当前标签文本及其递归分支节点
        for dict_case_item in dict_item.get("items", []) or []  # 按 formatter 顺序读取全部 case 分支
    ]

    # 固定键集合让后续 VG 规则不依赖 dataclass 字段顺序。
    return {
        "kind": dict_item.get("kind", ""),
        "header": dict_item.get("header", ""),
        "text": dict_item.get("text", ""),
        "label": dict_item.get("label", ""),
        "children": list_children,
        "alternate": list_alternate,
        "items": list_items,
    }

# _always_to_dict 转换 formatter always block 模型并补充控制流指标。
def _always_to_dict(item: Any) -> dict[str, Any]:
    """把 formatter always 块模型转换成 JSON 字典。

    参数:
        item: formatter always 块 dataclass、dict 或其他兼容对象。
    返回:
        包含触发条件、目标、左值、控制流和原始行的 always 报告字典。
    """

    # always 模型先标准化，便于补充控制流和左值统计指标。
    dict_item = _safe_dataclass_dict(item)  # always 块字段字典

    # lvalue 文本单独提取，方便质量门统计复杂赋值目标。
    list_lvalues: list[str] = []  # always 报告暴露的左值文本集合

    # 只保留 formatter 已解析为 dict 的左值条目。
    for dict_entry in dict_item.get("lvalues", []):

        # 非 dict 条目没有 text 字段，不能进入结构报告。
        if isinstance(dict_entry, dict):

            # text 字段是质量门用于展示的左值表达式。
            list_lvalues.append(dict_entry.get("text", ""))

    # 返回字段覆盖时钟、复位、控制结构和原始行。
    return {
        "header": dict_item.get("header", ""),
        "targets": dict_item.get("targets", []),
        "clock": dict_item.get("clock", ""),
        "reset": dict_item.get("reset", ""),
        "trigger_kind": dict_item.get("trigger_kind", "unknown"),
        "is_combinational": bool(dict_item.get("is_combinational", False)),
        "contains_case": bool(dict_item.get("contains_case", False)),
        "contains_if": bool(dict_item.get("contains_if", False)),
        "references_state": bool(dict_item.get("references_state", False)),
        "block_kind": dict_item.get("block_kind", ""),
        "target_count": len(dict_item.get("targets", []) or []),
        "lvalues": list_lvalues,
        "has_complex_lvalues": bool(dict_item.get("has_complex_lvalues", False)),
        "line_count": len(dict_item.get("lines", []) or []),
        "leading_comments": dict_item.get("leading_comments", []),
        "lines": dict_item.get("lines", []),
        "nodes": [
            _control_node_to_dict(dict_node)
            for dict_node in dict_item.get("nodes", []) or []
        ],
    }

# _instance_to_dict 转换 formatter instance 模型并保留实例身份。
def _instance_to_dict(item: Any) -> dict[str, Any]:
    """把 formatter 实例化模型转换成 JSON 字典。

    参数:
        item: formatter 实例化 dataclass、dict 或其他兼容对象。
    返回:
        包含模块名、实例名、参数化状态和原始文本的实例报告字典。
    """

    # 实例化对象先标准化，保留模块名、实例名和原始文本字段。
    dict_item = _safe_dataclass_dict(item)  # 实例化报告字段映射

    # 返回字段覆盖模块名、实例名、参数化状态和原始文本。
    return {
        "module_name": dict_item.get("module_name", ""),
        "instance_name": dict_item.get("instance_name", ""),
        "has_params": bool(dict_item.get("has_params", False)),
        "leading_comments": dict_item.get("leading_comments", []),
        "text": dict_item.get("text", ""),
    }

# _block_to_dict 转换 formatter block 模型并补充行数指标。
def _block_to_dict(item: Any) -> dict[str, Any]:
    """把 formatter 块模型转换成 JSON 字典。

    参数:
        item: formatter 普通块 dataclass、dict 或其他兼容对象。
    返回:
        块字段字典；含 lines 字段时额外补充 line_count。
    """

    # 普通块对象先标准化，随后只补充报告消费方需要的派生指标。
    dict_item = _safe_dataclass_dict(item)  # 普通块报告字段映射

    # 含 lines 字段的块额外暴露行数。
    if "lines" in dict_item:

        # line_count 让报告使用方无需重复计算。
        dict_item["line_count"] = len(dict_item.get("lines") or [])  # 当前块的原始行数

    # 返回转换后的块字段。
    return dict_item

# _attach_module_line_spans 为 formatter AST 条目补充源码行号范围。
def _attach_module_line_spans(
    dict_module: dict[str, Any],
    str_source: str,
    str_module_text: str,
    int_module_offset: int,
) -> None:
    """为单个 module 报告补充行号范围。

    参数:
        dict_module: formatter parser 已生成的 module 报告字典。
        str_source: 完整 Verilog 源文本。
        str_module_text: formatter 切出的当前 module 文本。
        int_module_offset: 当前 module 文本在完整源文本中的字符偏移。
    返回:
        本函数原地补充 span 字段，不返回业务值。
    """

    # 未能在原始文本中回贴 module 时保持缺失状态，让质量门报告不可信 span。
    if int_module_offset < 0:

        # 缺少 module span 比伪造行号更安全。
        return

    # module 行范围来自完整源文本偏移，保证 header 行数也被计入。
    tuple_module_span = _line_span_from_offsets(  # module 声明到 endmodule 的文件级行号范围
        str_source,  # 完整 Verilog 文本，用于统计文件级行号
        int_module_offset,  # 当前 module 在完整文本中的起始字符偏移
        int_module_offset + len(str_module_text),  # 当前 module 的非包含结束偏移
    )  # module 在完整文件中的一基行号范围

    # module 顶层 span 是 VG050 可信度检查的根节点证据。
    _set_line_span(dict_module, tuple_module_span[0], tuple_module_span[1])

    # 模块内部定位以 module 起始行为基准。
    list_module_lines = str_module_text.splitlines()  # 当前 module 的逐行文本

    # 参数声明按参数名回贴源码行，覆盖 ANSI parameter 列表。
    _attach_token_collection_spans(dict_module, "params", "name", list_module_lines, tuple_module_span[0])

    # 端口声明按端口名回贴源码行，供端口注释和命名规则定位。
    _attach_token_collection_spans(dict_module, "ports", "name", list_module_lines, tuple_module_span[0])

    # localparam 需要行号来区分配置参数和状态参数区域。
    _attach_token_collection_spans(dict_module, "localparams", "name", list_module_lines, tuple_module_span[0])

    # 内部声明 span 支撑信号区域和语义注释检查。
    _attach_token_collection_spans(dict_module, "decls", "name", list_module_lines, tuple_module_span[0])

    # 连续赋值使用 lhs 与 assign 关键字共同定位。
    _attach_assign_line_spans(dict_module, list_module_lines, tuple_module_span[0])

    # always 块依赖首行和 formatter 行数恢复完整范围。
    _attach_always_line_spans(dict_module, list_module_lines, tuple_module_span[0])

    # 实例化块使用实例名作为锚点，避免和模块类型名混淆。
    _attach_instance_line_spans(dict_module, list_module_lines, tuple_module_span[0])

    # 其他块结构使用 formatter 暴露的文本或行集合做保守定位。
    for str_key in ("generates", "initials", "functions", "tasks", "raw_blocks", "conditionals"):

        # 块集合定位失败时保留缺失 span，由质量门决定是否阻断。
        _attach_block_collection_spans(dict_module, str_key, list_module_lines, tuple_module_span[0])

# _attach_token_collection_spans 按名称 token 标注简单结构行号。
def _attach_token_collection_spans(
    dict_module: dict[str, Any],
    str_collection_name: str,
    str_token_key: str,
    list_module_lines: list[str],
    int_module_line_start: int,
) -> None:
    """为参数、端口和声明类 AST 条目补充行号。

    参数:
        dict_module: formatter AST 中的 module 报告字典。
        str_collection_name: 需要处理的 module 集合字段。
        str_token_key: 集合条目中用于定位的 token 字段名。
        list_module_lines: 当前 module 的逐行源码文本。
        int_module_line_start: 当前 module 在完整文件中的起始行号。
    返回:
        本函数原地补充集合条目的 span 字段。
    """

    # 每个集合单独维护游标，避免同集合重复 token 命中前一行。
    int_cursor = 0  # 当前集合下一次查找的 module 内部行索引

    # 逐个结构条目按声明顺序定位。
    for dict_item in dict_module.get(str_collection_name, []) or []:

        # token 为空时无法安全定位。
        str_token = str(dict_item.get(str_token_key) or "")  # 当前条目定位 token

        # 缺 token 的条目交由 VG050 报告缺 span。
        if not str_token:

            # 继续处理后续条目。
            continue

        # 在 module 文本中查找包含 token 的源码行。
        int_line_index = _find_line_index_containing(list_module_lines, str_token, int_cursor)  # token 命中的 module 内行索引

        # 简单声明命中后按单行结构写入 span。
        if int_line_index >= 0:

            # 真实文件行号需要加上 module 起始行偏移。
            int_line_number = int_module_line_start + int_line_index  # 当前条目文件行号

            # 参数、端口和声明条目目前都由 formatter 抽取为单行结构。
            _set_line_span(dict_item, int_line_number, int_line_number)

            # 下一次查找从当前行后继续。
            int_cursor = int_line_index + 1  # 当前集合搜索游标

# _attach_assign_line_spans 为 assign 条目补充行号范围。
def _attach_assign_line_spans(
    dict_module: dict[str, Any],
    list_module_lines: list[str],
    int_module_line_start: int,
) -> None:
    """为连续赋值语句补充行号范围。

    参数:
        dict_module: formatter AST 中的 module 报告字典。
        list_module_lines: 当前 module 的逐行源码文本。
        int_module_line_start: 当前 module 在完整文件中的起始行号。
    返回:
        本函数原地补充 assign 条目的 span 字段。
    """

    # assign 语句按源码顺序查找，避免端口声明中的同名信号误命中。
    int_cursor = 0  # assign 查找游标

    # 每个 assign 使用 lhs 和 assign 关键字共同定位。
    for dict_item in dict_module.get("assigns", []) or []:

        # lhs 是连续赋值最稳定的结构 token。
        str_lhs = str(dict_item.get("lhs") or "")  # 当前 assign 左值

        # 无左值时无法定位。
        if not str_lhs:

            # 继续处理其他 assign。
            continue

        # 查找同时包含 assign 和左值的源码行。
        int_line_index = _find_line_index_matching(list_module_lines, ("assign", str_lhs), int_cursor)  # assign 左值所在的源码行索引

        # 连续赋值命中后按单行 assign 记录 span。
        if int_line_index >= 0:

            # 计算完整文件行号。
            int_line_number = int_module_line_start + int_line_index  # 当前 assign 文件行号

            # 连续 assign 当前由 formatter 解析为单行语句。
            _set_line_span(dict_item, int_line_number, int_line_number)

            # 推进游标避免重复命中。
            int_cursor = int_line_index + 1  # assign 搜索游标

# _attach_always_line_spans 为 always 块补充行号范围。
def _attach_always_line_spans(
    dict_module: dict[str, Any],
    list_module_lines: list[str],
    int_module_line_start: int,
) -> None:
    """为 always 块补充行号范围。

    参数:
        dict_module: formatter AST 中的 module 报告字典。
        list_module_lines: 当前 module 的逐行源码文本。
        int_module_line_start: 当前 module 在完整文件中的起始行号。
    返回:
        本函数原地补充 always 条目的 span 字段。
    """

    # always 按顺序定位，适配同模块多个 always。
    int_cursor = 0  # 下一次 always 搜索的 module 内起点

    # 遍历 formatter 已识别的 always 块。
    for dict_item in dict_module.get("always", []) or []:

        # header 是 always 块最稳定的首行文本。
        str_header = str(dict_item.get("header") or "always")  # always 首行锚点文本

        # 查找包含 always 关键字且尽量包含 header 的行。
        int_line_index = _find_always_line_index(list_module_lines, str_header, int_cursor)  # always 首行索引

        # 找到后根据 formatter 暴露的行数计算结束行。
        if int_line_index >= 0:

            # line_count 至少为 1，避免空 lines 导致结束行早于起始行。
            int_line_count = max(int(dict_item.get("line_count") or 0), 1)  # always 块覆盖的源码行数

            # 起始行映射到完整文件坐标。
            int_line_number = int_module_line_start + int_line_index  # always 起始文件行号

            # always span 覆盖 reset/FSM 规则需要读取的完整块体。
            _set_line_span(dict_item, int_line_number, int_line_number + int_line_count - 1)

            # 下一轮从当前 always 块之后继续，避免重复命中同一块。
            int_cursor = int_line_index + int_line_count  # 下一个 always 的搜索起点

# _attach_instance_line_spans 为实例化条目补充行号范围。
def _attach_instance_line_spans(
    dict_module: dict[str, Any],
    list_module_lines: list[str],
    int_module_line_start: int,
) -> None:
    """为模块实例化补充行号范围。

    参数:
        dict_module: formatter AST 中的 module 报告字典。
        list_module_lines: 当前 module 的逐行源码文本。
        int_module_line_start: 当前 module 在完整文件中的起始行号。
    返回:
        本函数原地补充实例化条目的 span 字段。
    """

    # 实例化按顺序定位。
    int_cursor = 0  # 实例化查找游标

    # 遍历实例化报告。
    for dict_item in dict_module.get("instances", []) or []:

        # 实例名比模块名更能区分同类例化。
        str_instance_name = str(dict_item.get("instance_name") or "")  # 当前实例名

        # 缺实例名时无法可靠定位。
        if not str_instance_name:

            # 继续处理其他实例。
            continue

        # 查找包含实例名的源码行。
        int_line_index = _find_line_index_containing(list_module_lines, str_instance_name, int_cursor)  # 实例首行索引

        # 找到后按 text 行数估算 span。
        if int_line_index >= 0:

            # text 字段来自 formatter，通常覆盖完整实例化语句。
            int_line_count = max(len(str(dict_item.get("text") or "").splitlines()), 1)  # 实例化文本行数

            # 实例首行需要从 module 内坐标换算到完整文件坐标。
            int_line_number = int_module_line_start + int_line_index  # 实例起始文件行号

            # 实例 span 覆盖端口连接列表，便于后续注释或区域审查。
            _set_line_span(dict_item, int_line_number, int_line_number + int_line_count - 1)

            # 后续实例从当前连接列表之后继续定位。
            int_cursor = int_line_index + int_line_count  # 下一个实例的搜索起点

# _attach_block_collection_spans 为普通块集合补充保守行号范围。
def _attach_block_collection_spans(
    dict_module: dict[str, Any],
    str_collection_name: str,
    list_module_lines: list[str],
    int_module_line_start: int,
) -> None:
    """为 generate、initial、function、task 等块补充行号范围。

    参数:
        dict_module: formatter AST 中的 module 报告字典。
        str_collection_name: 需要处理的块集合字段。
        list_module_lines: 当前 module 的逐行源码文本。
        int_module_line_start: 当前 module 在完整文件中的起始行号。
    返回:
        本函数原地补充块条目的 span 字段。
    """

    # 每类块按源码顺序定位。
    int_cursor = 0  # 块集合查找游标

    # 遍历当前块集合。
    for dict_item in dict_module.get(str_collection_name, []) or []:

        # generate 需定位外层关键字。
        if str_collection_name == "generates":

            # 从前一块后继续查找。
            int_line_index = _find_line_index_containing(list_module_lines, "generate", int_cursor)  # generate 起始索引

            # 结束行从起点后查找。
            int_end_index = _find_line_index_containing(list_module_lines, "endgenerate", int_line_index + 1)  # generate 结束索引

            # 关键字完整时记录范围。
            if int_line_index >= 0 and int_end_index >= int_line_index:

                # 范围覆盖外层关键字。
                _set_line_span(
                    dict_item,
                    int_module_line_start + int_line_index,
                    int_module_line_start + int_end_index,
                )

                # 游标移到当前块后。
                int_cursor = int_end_index + 1  # 后续 generate 的起点

                # 跳过普通块估算。
                continue

        # 选择可用于定位的文本片段。
        str_anchor = _block_anchor_text(dict_item)  # 当前块定位文本

        # 缺锚点时保持缺失 span。
        if not str_anchor:

            # 继续处理后续块。
            continue

        # 查找块首行。
        int_line_index = _find_line_index_containing(list_module_lines, str_anchor, int_cursor)  # 普通块首行索引

        # 找到后按 line_count 或文本行数估算结束行。
        if int_line_index >= 0:

            # line_count 优先使用 formatter 已经计算的值。
            int_line_count = max(int(dict_item.get("line_count") or len(str_anchor.splitlines()) or 1), 1)  # 普通块覆盖行数

            # 映射到完整文件行号。
            int_line_number = int_module_line_start + int_line_index  # 块起始文件行号

            # 普通块 span 保守覆盖首行到 formatter 估算的尾行。
            _set_line_span(dict_item, int_line_number, int_line_number + int_line_count - 1)

            # 同类块后续定位从当前块尾部继续。
            int_cursor = int_line_index + int_line_count  # 下一个普通块的搜索起点

# _line_span_from_offsets 把字符偏移转换为一基行号范围。
def _line_span_from_offsets(str_source: str, int_start: int, int_end: int) -> tuple[int, int]:
    """把源码字符偏移转换为一基行号范围。

    参数:
        str_source: 完整源文本。
        int_start: 起始字符偏移。
        int_end: 结束字符偏移。
    返回:
        返回一基起始行号和结束行号。
    """

    # 起始行号统计起点之前的换行数量。
    int_line_start = str_source[:int_start].count("\n") + 1  # 一基起始行号

    # 结束行号使用非包含结束偏移，空文本时仍保持起始行。
    int_line_end = str_source[: max(int_start, int_end - 1)].count("\n") + 1  # 一基结束行号

    # 返回 span 元组。
    return int_line_start, max(int_line_start, int_line_end)

# _set_line_span 统一写入 formatter AST 的行号范围。
def _set_line_span(dict_item: dict[str, Any], int_line_start: int, int_line_end: int) -> None:
    """
    写入 AST 条目的源码行号范围。

    参数:
        dict_item: 需要补充 span 的 formatter AST 条目。
        int_line_start: 条目在完整文件中的一基起始行。
        int_line_end: 条目在完整文件中的一基结束行。
    返回:
        本函数原地更新 AST 条目，不返回业务值。
    """

    # 起始行保留原始定位结果，供 VG050 判断 span 是否可信。
    dict_item["line_start"] = int_line_start  # AST 条目源码起始行

    # 结束行向前夹紧到起始行之后，避免调用方传入反向范围。
    dict_item["line_end"] = max(int_line_start, int_line_end)  # AST 条目源码结束行

# _find_line_index_containing 查找包含指定文本的 module 内行号。
def _find_line_index_containing(list_lines: list[str], str_token: str, int_start: int) -> int:
    """查找从指定行开始第一个包含 token 的行索引。

    参数:
        list_lines: 待搜索的源码行列表。
        str_token: 需要匹配的文本 token。
        int_start: 搜索起始行索引。
    返回:
        找到时返回零基行索引，否则返回 -1。
    """

    # 空 token 不能作为可靠锚点。
    if not str_token:

        # 返回未命中标记。
        return -1

    # 从游标开始顺序查找。
    for int_index in range(max(int_start, 0), len(list_lines)):

        # token 直接包含即视为命中。
        if str_token in list_lines[int_index]:

            # 顺序查找命中当前 token 所在的源码行。
            return int_index

    # 游标之后未命中时从文件开头兜底查找。
    for int_index, str_line in enumerate(list_lines):

        # 兜底查找用于处理 formatter 重排后的小范围顺序差异。
        if str_token in str_line:

            # 兜底命中说明 formatter 顺序和源码游标发生偏移。
            return int_index

    # 未命中时让调用方保留缺失 span。
    return -1

# _find_line_index_matching 查找同时包含多个 token 的源码行。
def _find_line_index_matching(list_lines: list[str], tuple_tokens: tuple[str, ...], int_start: int) -> int:
    """查找同时包含多个 token 的行索引。

    参数:
        list_lines: 待搜索的源码行列表。
        tuple_tokens: 同一行必须包含的文本 token。
        int_start: 搜索起始行索引。
    返回:
        找到时返回零基行索引，否则返回 -1。
    """

    # 从游标开始优先查找，保持多条 assign 的顺序。
    for int_index in range(max(int_start, 0), len(list_lines)):

        # 所有 token 同时出现才算命中。
        if all(str_token in list_lines[int_index] for str_token in tuple_tokens):

            # 顺序命中同时包含所有 token 的源码行。
            return int_index

    # 兜底从头查找，适配 formatter 提取顺序与源码略有差异的情况。
    for int_index, str_line in enumerate(list_lines):

        # 兜底阶段仍要求全部 token 同时出现，避免误贴到只含 lhs 的注释行。
        if all(str_token in str_line for str_token in tuple_tokens):

            # 兜底命中处理 formatter 抽取顺序的小幅漂移。
            return int_index

    # 没有源码行能承载该 always header 时返回缺失标记。
    return -1

# _find_always_line_index 专门定位 always 首行。
def _find_always_line_index(list_lines: list[str], str_header: str, int_start: int) -> int:
    """定位 always 块首行。

    参数:
        list_lines: 当前 module 的源码行列表。
        str_header: formatter 提取的 always header 文本。
        int_start: 搜索起始行索引。
    返回:
        找到时返回零基行索引，否则返回 -1。
    """

    # 去掉空白后降低 always@( 和 always @ 的格式差异。
    str_normalized_header = "".join(str_header.split())  # 去空白后的 always header 锚点

    # 从游标开始查找 always 行。
    for int_index in range(max(int_start, 0), len(list_lines)):

        # 当前行去空白后用于宽松匹配。
        str_normalized_line = "".join(list_lines[int_index].split())  # 去空白后的源码候选行

        # 优先匹配完整 header，退而要求包含 always 关键字。
        if str_normalized_header in str_normalized_line or "always" in str_normalized_line:

            # 命中 always 首行后返回 module 内部行索引。
            return int_index

    # 未命中时返回 -1。
    return -1

# _block_anchor_text 提取普通块可用于定位的文本。
def _block_anchor_text(dict_item: dict[str, Any]) -> str:
    """返回普通块定位所需的首个非空文本锚点。

    参数:
        dict_item: formatter AST 中的普通块条目。
    返回:
        可用于源码行查找的文本片段。
    """

    # lines 字段最能代表块首行。
    list_lines = dict_item.get("lines") or []  # formatter 暴露的块行集合

    # 优先取第一条非空行。
    for str_line in list_lines:

        # 去掉外侧空白后判断是否可用。
        str_stripped = str(str_line).strip()  # 候选块首行

        # 非空行可作为锚点。
        if str_stripped:

            # 返回当前锚点。
            return str_stripped

    # text 字段作为实例以外块的兜底来源。
    str_text = str(dict_item.get("text") or "").strip()  # 块文本兜底锚点

    # 只返回首行，避免跨行片段无法匹配单行源码。
    return str_text.splitlines()[0].strip() if str_text else ""

# _safe_dataclass_dict 统一处理 formatter 内部数据对象。
def _safe_dataclass_dict(item: Any) -> dict[str, Any]:
    """把 dataclass、dict 或其他对象安全转成字典。

    参数:
        item: formatter 内部模型、dict、None 或其他可转文本对象。
    返回:
        JSON 友好的浅层字段字典。
    """

    # None 表示 formatter 没有提供该结构。
    if item is None:

        # 空字典保持调用方字段访问安全。
        return {}

    # dataclass 是 formatter 后端最常见的结构模型。
    if is_dataclass(item):

        # asdict 递归转换 dataclass 字段。
        return asdict(item)

    # dict 输入复制一份，避免调用方修改原对象。
    if isinstance(item, dict):

        # 浅拷贝足够保护外层字段。
        return dict(item)

    # 其他对象保留文本表示，避免信息完全丢失。
    return {"text": str(item)}

# _source_metrics 计算轻量文本指标。
def _source_metrics(source: str) -> dict[str, Any]:
    """计算 AST 报告需要的源文本指标。

    参数:
        source: 待统计的 Verilog 源文本。
    返回:
        包含行数、注释、缩进和 header 特征的轻量指标字典。
    """

    # splitlines 不保留换行符，适合统计行级指标。
    list_lines = source.splitlines()  # 源文本行列表

    # 非空行用于估算有效代码密度。
    list_nonempty_lines = [str_line for str_line in list_lines if str_line.strip()]  # 非空行列表

    # line comment 统计用于注释覆盖诊断。
    list_comment_lines = [str_line for str_line in list_lines if "//" in str_line]  # 含行注释的行列表

    # 返回轻量指标，避免在质量门里重复扫描源文本。
    return {
        "lines": len(list_lines),
        "nonempty_lines": len(list_nonempty_lines),
        "line_comments": len(list_comment_lines),
        "block_comment_markers": source.count("/*") + source.count("*/"),
        "tab_indented_lines": sum(1 for str_line in list_lines if str_line.startswith("\t")),
        "space_indented_lines": sum(1 for str_line in list_lines if str_line.startswith(" ")),
        "has_timescale": "`timescale" in source,
        "has_bilingual_header": "English" in source and "Chinese" in source,
    }

# _tree_reports_are_ok 聚合目录级 ok 标志。
def _tree_reports_are_ok(
    list_file_reports: list[dict[str, Any]],
    list_diagnostics: list[dict[str, Any]],
) -> bool:
    """判断目录级 AST 报告是否整体通过。

    参数:
        list_file_reports: 目录下每个文件的 AST 报告列表。
        list_diagnostics: 已聚合的目录级诊断列表。
    返回:
        全部文件 ok 且没有 error 诊断时返回 True。
    """

    # 单文件 ok 和聚合诊断必须同时通过。
    return all(dict_report.get("ok") for dict_report in list_file_reports) and not any(
        dict_item.get("severity") == "error"
        for dict_item in list_diagnostics
    )

# _tree_summary 生成目录级报告摘要。
def _tree_summary(
    list_file_reports: list[dict[str, Any]],
    list_diagnostics: list[dict[str, Any]],
) -> dict[str, int]:
    """生成目录级 AST 报告摘要。

    参数:
        list_file_reports: 目录下每个文件的 AST 报告列表。
        list_diagnostics: 已聚合的目录级诊断列表。
    返回:
        包含文件数、module 数、error 数和 warning 数的摘要字典。
    """

    # parse error 与 formatter mismatch 分开汇总，供公开 AST 门禁独立展示。
    int_parse_errors = sum(1 for dict_item in list_diagnostics if dict_item.get("severity") == "error")  # 目录解析错误总数

    # 逐文件 formatter violation 数量反映模板一致性错误规模。
    int_formatter_errors = sum(  # 目录 formatter 模板错误总数
        len(dict_report.get("formatter_violations", []))  # 当前文件的模板不一致条数
        for dict_report in list_file_reports  # 从每份单文件报告读取模板差异列表
    )

    # summary 在保留旧字段的同时新增独立错误来源。
    return {
        "files": len(list_file_reports),
        "modules": sum(len(dict_report.get("modules", [])) for dict_report in list_file_reports),
        "parse_errors": int_parse_errors,
        "formatter_errors": int_formatter_errors,
        "errors": int_parse_errors + int_formatter_errors,
        "warnings": sum(1 for dict_item in list_diagnostics if dict_item.get("severity") == "warning"),
    }

# _diagnostic 统一构造 AST 诊断字典。
def _diagnostic(severity: str, code: str, message: str, *, line: int | None = None) -> dict[str, Any]:
    """构造 formatter_ast 来源的诊断字典。

    参数:
        severity: 诊断级别，例如 error 或 warning。
        code: 诊断代码。
        message: 面向调用方展示的诊断消息。
        line: 可选源文件行号。
    返回:
        与 validation.py 消费契约兼容的诊断字典。
    """

    # 诊断字段保持 validation.py 消费契约。
    return {
        "severity": severity,
        "code": code,
        "message": message,
        "line": line,
        "source": "formatter_ast",
    }
