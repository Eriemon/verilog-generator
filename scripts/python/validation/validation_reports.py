"""提供 validation issue 整形、artifact 清点与报告对象装配辅助。"""

# future annotations 避免运行期解析 Path 与报告类型提示。
from __future__ import annotations

# Path 负责报告中的 artifact 根目录字段与相对路径计算。
from pathlib import Path

# Any 兼容 verifier 原始字典与 metrics 字段。
from typing import Any

# ValidationIssue/ValidationReport 是 validation facade 的稳定报告契约。
from .validation_models import ValidationIssue, ValidationReport

# VERILOG_EXTENSIONS 限定 validation 扫描的 RTL 文件范围。
VERILOG_EXTENSIONS = {".v"}  # Verilog-2001 源文件后缀集合

# BLOCKED_ARTIFACT_PARTS 防止开发产物进入生成目录或发布包。
BLOCKED_ARTIFACT_PARTS = {"tests", "smoke", "reports", "runs", "_smoke_runs", "__pycache__", ".pytest_cache"}  # 禁止嵌入的开发产物目录名

# 构造只包含缺失路径诊断的 validation 报告。
def build_missing_generated_path_report(path_root: Path) -> ValidationReport:
    """构造只包含缺失路径诊断的 validation 报告。

    :param path_root: 调用方提供的生成 artifact 根目录。
    :return: 仅带 spec_issue 的 ValidationReport。
    """

    # 缺失目录时不附加其他 metrics，保持旧报告形状。
    return ValidationReport(
        "rtl",
        path_root,
        (
            ValidationIssue(
                "error",
                "Generated path does not exist.",
                str(path_root),
                source="spec_issue",
            ),
        ),
        {},
    )

# 构造 validation 主流程使用的完整报告对象。
def build_validation_report(
    path_root: Path,
    list_issues: list[ValidationIssue],
    dict_metrics: dict[str, Any],
) -> ValidationReport:
    """构造 validation 主流程使用的完整报告对象。

    :param path_root: 本轮 validation 的 artifact 根目录。
    :param list_issues: 已累计完成的 validation 诊断列表。
    :param dict_metrics: 已累计完成的 validation 结构化度量。
    :return: 保持历史字段结构的 ValidationReport。
    """

    # issues 继续冻结成 tuple，保持报告对象的只读契约。
    return ValidationReport("rtl", path_root, tuple(list_issues), dict_metrics)

# 转换 interface contract gate 的原始问题。
def contract_gate_issues_from_verifier(list_raw_issues: list[dict[str, Any]]) -> list[ValidationIssue]:
    """转换 interface contract gate 的原始问题。

    :param list_raw_issues: verifier 输出的字典诊断列表。
    :return: 统一 ValidationIssue 模型下的接口合同诊断。
    """

    # interface verifier 的 path/source 字段在这里收敛到统一报告模型。
    list_issues: list[ValidationIssue] = []  # 统一接口诊断列表

    # 每个 verifier issue 保留 severity/message/path/source/case_id。
    for dict_item in list_raw_issues:

        # 当前 interface issue 转换为 validation 统一模型。
        list_issues.append(
            ValidationIssue(
                str(dict_item.get("severity", "error")),
                str(dict_item.get("message", "Interface contract issue.")),
                dict_item.get("path"),
                "static",
                str(dict_item.get("source", "current_module_issue")),
                dict_item.get("case_id"),
            )
        )

    # 返回转换后的诊断列表。
    return list_issues

# 返回缺失输出 artifact 的诊断。
def _validate_expected_outputs(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    """
    返回缺失输出 artifact 的诊断。

    :param spec: 已归一化的 Verilog 规格。
    :param root: 生成 artifact 根目录。
    :return: spec outputs 中声明但实际不存在的 artifact 诊断。
    """

    # expected outputs gate 只检查声明产物存在性，不验证 RTL 语义。
    list_issues: list[ValidationIssue] = []  # 缺失输出诊断列表

    # outputs 是 spec 中声明的生成产物列表。
    list_outputs = spec.get("outputs", []) if isinstance(spec.get("outputs", []), list) else []  # 规格输出列表

    # 逐个检查输出路径。
    for dict_output in list_outputs:

        # 非 dict 输出项跳过，normalize_spec 已负责更严格合同。
        if not isinstance(dict_output, dict):

            # 继续检查后续输出项。
            continue

        # str_rel_path 是输出 artifact 相对路径。
        str_rel_path = _output_rel_path(dict_output)  # 输出 artifact 相对路径

        # 未声明路径时跳过。
        if not str_rel_path:

            # 空路径不是文件存在性检查对象。
            continue

        # path_output 是输出 artifact 的实际路径。
        path_output = root / str_rel_path  # 期望存在的输出文件

        # 缺失文件生成 spec_issue。
        if not path_output.exists():

            # 记录缺失输出。
            list_issues.append(
                ValidationIssue(
                    "error",
                    "Expected output artifact is missing.",
                    str_rel_path,
                    "static",
                    "spec_issue",
                )
            )

    # 返回缺失输出诊断。
    return list_issues

# 判断当前路径是否仍是可访问文件。
def _path_is_existing_file(path_item: Path) -> bool:
    """
    判断当前路径是否仍是可访问文件。

    :param path_item: 正在检查的候选路径。
    :return: 路径当前仍存在且是文件时返回 True；瞬态缺失时返回 False。
    """

    # is_file 在竞态删除时可能抛出 FileNotFoundError，这里统一降级成 False。
    try:

        # 当前路径仍是文件时，允许调用方继续把它作为 artifact 候选。
        return path_item.is_file()

    # 文件在扫描期间瞬态消失时，直接按“不存在可读文件”处理。
    except FileNotFoundError:

        # 返回 False 让上层继续扫描其他仍存在的路径。
        return False

# 判断当前路径是否仍是可访问目录。
def _path_is_existing_dir(path_item: Path) -> bool:
    """
    判断当前路径是否仍是可访问目录。

    :param path_item: 正在检查的候选路径。
    :return: 路径当前仍存在且是目录时返回 True；瞬态缺失时返回 False。
    """

    # is_dir 在并发清理目录时也可能抛出 FileNotFoundError，这里统一吞掉。
    try:

        # 当前路径仍是目录时，允许树遍历继续向下展开。
        return path_item.is_dir()

    # 目录在遍历期间被并发删除时，不让整个扫描流程失败。
    except FileNotFoundError:

        # 返回 False 表示当前路径不再可继续深入遍历。
        return False

# 返回 artifact 根目录下当前仍可访问的后代路径列表。
def _iter_existing_tree_paths(root: Path) -> list[Path]:
    """
    返回 artifact 根目录下当前仍可访问的后代路径列表。

    :param root: 需要扫描的 artifact 根目录。
    :return: 按 POSIX 路径排序的当前可访问后代路径列表。
    """

    # list_paths_unsorted 暂存扫描过程中观察到的真实后代路径。
    list_paths_unsorted: list[Path] = []  # 尚未排序的可访问后代路径列表

    # list_pending_dirs 保存后续还要继续展开的目录。
    list_pending_dirs: list[Path] = [root]  # 待展开的目录栈

    # 目录栈为空时表示所有仍可访问的后代都已扫描完成。
    while list_pending_dirs:

        # path_current_dir 是这轮准备展开的目录。
        path_current_dir = list_pending_dirs.pop()  # 当前准备展开的目录路径

        # 当前目录如果在展开前就被删掉，需要把这次缺失当作瞬态竞态处理。
        try:

            # 先读取当前目录的直接子项，避免深层目录缺失时整轮遍历失败。
            list_children = list(path_current_dir.iterdir())  # 当前目录下仍可见的直接子项

        # 瞬态缺失目录或误命中文件路径时，都直接跳过当前分支。
        except (FileNotFoundError, NotADirectoryError):

            # 继续处理其他仍然存在的目录分支。
            continue

        # 逐个记录当前目录下仍可访问的子项。
        for path_child in list_children:

            # 先把子项登记到结果列表，供上层继续做文件/目录筛选。
            list_paths_unsorted.append(path_child)

            # 当前子项如果仍是目录，则继续加入待展开栈。
            if _path_is_existing_dir(path_child):

                # 目录子项需要继续展开，才能扫描更深层的 artifact。
                list_pending_dirs.append(path_child)

    # 最终输出按 POSIX 文本排序，保持 JSON 报告和单测断言稳定。
    return sorted(list_paths_unsorted, key=lambda path_current: path_current.as_posix())

# 返回未声明或类型不合规 artifact 的诊断。
def _validate_declared_artifact_tree(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    """
    返回未声明或类型不合规 artifact 的诊断。

    :param spec: 已归一化的 Verilog 规格，提供允许输出白名单。
    :param root: 生成 artifact 根目录。
    :return: 未在 spec outputs 中声明或类型不被允许的文件诊断。
    """

    # set_declared_paths 是 spec outputs 中允许出现的相对路径集合。
    set_declared_paths = _declared_output_paths(spec)  # 已声明输出路径集合

    # list_issues 保存额外文件诊断。
    list_issues: list[ValidationIssue] = []  # artifact 白名单诊断集合

    # 逐个文件检查是否属于声明输出。
    for path_item in _iter_existing_tree_paths(root):

        # 目录不是 artifact 文件，跳过。
        if not _path_is_existing_file(path_item):

            # 继续检查下一个路径。
            continue

        # str_rel_path 统一使用 POSIX 分隔符，保持 JSON 报告和 smoke 断言稳定。
        str_rel_path = path_item.relative_to(root).as_posix()  # artifact 相对路径

        # 声明过的输出交给后续 Verilog、testbench 和 readiness gate 验证。
        if str_rel_path in set_declared_paths:

            # 当前文件是合法输出。
            continue

        # str_suffix 用于区分未声明 RTL 和其他旁路文件。
        str_suffix = path_item.suffix.lower()  # artifact 文件后缀

        # 未声明 .v 需要明确提示为额外 Verilog artifact。
        if str_suffix == ".v":

            # 记录额外 Verilog 文件。
            list_issues.append(
                ValidationIssue(
                    "error",
                    "Unexpected Verilog artifact was generated outside declared outputs.",
                    str_rel_path,
                    "static",
                    "spec_issue",
                )
            )

            # 当前未声明 Verilog 文件已记录，继续检查下一个 artifact。
            continue

        # 其他文件类型不属于 skill 当前发布 artifact 合同。
        list_issues.append(
            ValidationIssue(
                "error",
                "Only declared Verilog .v artifacts are allowed.",
                str_rel_path,
                "static",
                "spec_issue",
            )
        )

    # 返回所有额外 artifact 诊断。
    return list_issues

# 返回规格声明的输出文件相对路径集合。
def _declared_output_paths(spec: dict[str, Any]) -> set[str]:
    """
    返回规格声明的输出文件相对路径集合。

    :param spec: 已归一化的 Verilog 规格。
    :return: POSIX 风格的声明输出路径集合。
    """

    # obj_list_candidate_outputs 可能来自用户规格或 normalize_spec 默认输出，先按 object 收窄类型。
    obj_list_candidate_outputs: object = spec.get("outputs", [])  # artifact 白名单原始来源

    # list_outputs 只接受列表格式，防止异常配置扩大允许文件集合。
    list_outputs = obj_list_candidate_outputs if isinstance(obj_list_candidate_outputs, list) else []  # 可用于白名单的输出项

    # set_paths 保存归一化后的相对路径。
    set_paths: set[str] = set()  # 允许出现的输出文件路径集合

    # 逐个输出项提取 path 字段。
    for dict_output in list_outputs:

        # 非 dict 输出项不参与白名单。
        if not isinstance(dict_output, dict):

            # 继续检查下一个输出项。
            continue

        # str_rel_path 是即将进入允许集合的 output path。
        str_rel_path = _output_rel_path(dict_output)  # 待归一化的白名单路径

        # 空路径不加入白名单。
        if not str_rel_path:

            # 无法定位实际文件时不授予任何路径白名单。
            continue

        # Path.as_posix 统一 Windows 和 POSIX 分隔符。
        set_paths.add(Path(str_rel_path).as_posix())  # 归一化后的输出路径

    # 返回声明输出集合。
    return set_paths

# 返回 RTL 源文件和 testbench 文件。
def _rtl_files(root: Path) -> list[Path]:
    """
    返回 RTL 源文件和 testbench 文件。

    :param root: 生成 artifact 根目录。
    :return: 按路径排序的 Verilog 文件列表。
    """

    # list_files 保存排序后的 Verilog 文件。
    list_files = sorted(  # Verilog 文件列表
        path_item  # 匹配到的 Verilog 文件
        for path_item in _iter_existing_tree_paths(root)  # 递归扫描当前仍可访问的候选文件
        if _path_is_existing_file(path_item) and path_item.suffix.lower() in VERILOG_EXTENSIONS  # 只保留文件和支持后缀
    )

    # 返回扫描结果。
    return list_files

# 返回非 testbench 的 RTL 源文件。
def _rtl_source_files(root: Path) -> list[Path]:
    """
    返回非 testbench 的 RTL 源文件。

    :param root: 生成 artifact 根目录。
    :return: 排除 testbench 后的 RTL 源文件列表。
    """

    # list_sources 过滤掉 testbench 文件，供综合 readiness 使用。
    list_sources = [path_item for path_item in _rtl_files(root) if not _is_testbench(path_item)]  # 非 testbench RTL 文件列表

    # 返回源文件集合。
    return list_sources

# 返回生成目录中不应出现的开发产物诊断。
def _unexpected_artifact_issues(root: Path) -> list[ValidationIssue]:
    """
    返回生成目录中不应出现的开发产物诊断。

    :param root: 生成 artifact 根目录。
    :return: 开发产物目录或缓存泄漏诊断。
    """

    # list_issues 保存泄漏产物诊断。
    list_issues: list[ValidationIssue] = []  # 开发产物泄漏诊断

    # 扫描所有路径组件，发现禁入目录即报错。
    for path_item in _iter_existing_tree_paths(root):

        # set_parts 是当前路径的归一化组件集合。
        set_parts = {part.lower() for part in path_item.relative_to(root).parts}  # artifact 相对路径组件

        # 与禁入目录相交表示开发产物泄漏。
        if set_parts & BLOCKED_ARTIFACT_PARTS:

            # 记录泄漏路径。
            list_issues.append(
                ValidationIssue(
                    "error",
                    "Generated artifact tree contains development-only files.",
                    str(path_item.relative_to(root)),
                    "static",
                    "spec_issue",
                )
            )

    # 占位扫描结束后返回全部泄漏命中记录。
    return list_issues

# 返回输出 artifact 相对路径。
def _output_rel_path(dict_output: dict[str, Any]) -> str | None:
    """
    返回输出 artifact 相对路径。

    :param dict_output: spec outputs 中的单个输出声明。
    :return: 非空路径文本；缺少 path 或 path 为空时返回 None。
    """

    # obj_path 是输出项中的原始 path 字段。
    obj_path = dict_output.get("path")  # 输出项原始路径字段

    # 空路径返回 None。
    if obj_path is None:

        # 无路径时不做文件存在性检查。
        return None

    # str_path 是字符串化路径。
    str_path = str(obj_path)  # 输出 artifact 路径字符串

    # 空字符串视为无路径。
    if not str_path:

        # 空路径不参与检查。
        return None

    # 返回相对路径文本。
    return str_path

# 返回路径是否指向 testbench 文件。
def _is_testbench(path: Path) -> bool:
    """
    返回路径是否指向 testbench 文件。

    :param path: 待判断的 Verilog-like 文件路径。
    :return: 文件名符合常见 testbench 命名时返回 True。
    """

    # str_stem 是文件名主体的小写形式。
    str_stem = path.stem.lower()  # 小写文件名主体

    # 常见 testbench 命名都视为 testbench。
    return str_stem.endswith("_tb") or str_stem.startswith("tb_") or "testbench" in str_stem
