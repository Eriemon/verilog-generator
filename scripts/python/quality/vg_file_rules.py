"""收集 Verilog 文件角色事实并执行文件级命名门禁。"""

# future annotations 避免运行期解析递归类型。
from __future__ import annotations

# 正则表达式只识别已确认的终止版本或纯数字段。
import re

# dataclass 固定文件事实的不可变公共合同。
from dataclasses import dataclass, field

# Path 与纯路径类型分别处理文件系统和跨平台词法校验。
from pathlib import Path, PurePosixPath, PureWindowsPath

# Mapping 与 Sequence 描述规格和 formatter 报告的只读输入。
from typing import Any, Mapping, Sequence

# formatter AST 只在调用方尚未提供 `.v` 报告时构建一次。
from .formatter_ast import build_ast_report_for_path

# 统一结果模型保持文件门禁与现有语义引擎兼容。
from .vg_rule_models import VgEvaluation, VgFinding, failed, inconclusive, passed

# 支持的文件后缀限定本预检的发现边界。
VERILOG_FILE_EXTENSIONS = {".v", ".sv"}  # 允许参与文件预检的扩展名

# 显式测试目录采用精确目录段，普通 tests 不构成证据。
TESTBENCH_DIRECTORY_NAMES = {"tb", "testbench", "sim"}  # 明确表示测试代码的目录段

# 终止后缀先匹配版本段，再匹配无功能含义的纯数字段。
INVALID_SUFFIX_PATTERN = re.compile(  # 仅捕获文件主体末尾的独立违规段
    r"(?P<matched_suffix>[_-](?:(?P<version_kind>v|ver|version)\d+|\d+))$",  # 违规段结构
    re.IGNORECASE,  # 版本关键字大小写不敏感
)

# 内容证据按固定顺序输出，避免集合遍历造成报告漂移。
CONTENT_EVIDENCE_ORDER = (  # VG149 内容疑似 testbench 的稳定证据顺序
    "initial_process",  # initial 过程结构
    "simulation_task",  # 完整仿真系统任务调用
    "clock_stimulus",  # 延时驱动的时钟激励
    "dut_self_check",  # DUT 实例与自检控制组合
)

# VgFileFacts 固定跨 AST 边界共享的文件预检事实。
@dataclass(frozen=True)
class VgFileFacts:
    """保存单个 Verilog 或 SystemVerilog 文件的角色与读取事实。

    参数:
        path: 相对扫描根的规范化 POSIX 路径。
        extension: 保留原文件实际大小写的扩展名。
        role: 当前文件的 design、testbench 或 ambiguous 角色。
        role_source: 当前角色的确定性来源。
        role_evidence: 支撑角色判断的有序证据。
        confirmation_required: 宿主是否必须请求二次确认。
        confirmed_role: 用户已经提供的 design 或 testbench 角色。
        read_error: 不含绝对路径的稳定读取错误。
        source_path: 仅供内部读取的规范绝对路径。
    返回:
        不可变文件事实对象。
    """

    # path 是所有公开 finding 和确认键的稳定身份。
    path: str  # 相对扫描根的 POSIX 文件路径

    # extension 保留调用方文件名中的实际大小写。
    extension: str  # 当前文件的 .v 或 .sv 扩展名

    # role 仅使用冻结合同声明的三个值。
    role: str  # 文件预检当前确定的 design、testbench 或 ambiguous 角色

    # role_source 表示名称、目录、确认或内容证据来源。
    role_source: str  # 当前角色判定的确定性来源

    # role_evidence 保留全部命中的显式证据。
    role_evidence: tuple[str, ...]  # 有序且去重的角色证据

    # confirmation_required 区分普通 design 与待确认文件。
    confirmation_required: bool  # 是否需要宿主请求用户确认

    # confirmed_role 只保存已经通过输入校验的角色。
    confirmed_role: str | None  # 用户确认的文件角色

    # read_error 让文件门禁保持 fail-closed。
    read_error: str | None  # 稳定且不泄露绝对路径的读取诊断

    # source_path 不参与 repr、比较或公开序列化。
    source_path: Path = field(repr=False, compare=False)  # 内部规范绝对文件路径

    # to_dict 只导出稳定公共事实，不泄露本机路径。
    def to_dict(self) -> dict[str, Any]:
        """返回不包含内部 source_path 的 JSON 友好字典。

        参数:
            self: 当前文件事实对象。
        返回:
            只包含稳定公共字段的字典。
        """

        # 显式列出公开字段，避免 dataclass 自动序列化绝对路径。
        return {
            "path": self.path,  # 对外稳定的相对文件身份
            "extension": self.extension,  # 保留实际大小写的扩展名
            "role": self.role,  # 当前角色判定结果
            "role_source": self.role_source,  # 当前角色的主要来源
            "role_evidence": list(self.role_evidence),  # 可序列化的全部角色证据
            "confirmation_required": self.confirmation_required,  # 宿主是否需要二次确认
            "confirmed_role": self.confirmed_role,  # 已校验的用户确认结果
            "read_error": self.read_error,  # 不泄露本机路径的读取错误
        }

# collect_vg_file_facts 是文件发现与确认映射校验的唯一入口。
def collect_vg_file_facts(
    root: Path,
    spec: Mapping[str, Any] | None,
    *,
    reports: Sequence[Mapping[str, Any]] = (),
) -> tuple[VgFileFacts, ...]:
    """收集扫描根下的文件事实并校验可选角色确认。

    参数:
        root: 单个 `.v/.sv` 文件或包含这些文件的目录。
        spec: 可选规格，其中可包含 file_role_confirmations 映射。
        reports: 已有 formatter 报告；`.v` 内容结构证据优先复用该输入。
    返回:
        按规范化路径稳定排序的不可变文件事实。
    异常:
        ValueError: 扫描根或确认映射不满足冻结合同时抛出。
    """

    # 解析扫描根，确保内部路径比较不受调用目录影响。
    path_root = root.resolve()  # 本轮预检的规范文件或目录

    # 发现所有受文件命名门禁约束的来源。
    tuple_sources = _discover_sources(path_root)  # 稳定排序后的 .v/.sv 路径

    # 预先构建公开路径到绝对路径的唯一映射。
    dict_sources: dict[str, Path] = {}  # 确认映射允许引用的当前文件集合

    # 逐个登记公开路径，便于保持身份转换过程可读。
    for path_source in tuple_sources:

        # 相对路径是确认输入与内部绝对路径之间的稳定键。
        str_relative_path = _relative_path(path_root, path_source)  # 当前文件的公开身份

        # 当前扫描中的每个公开身份只绑定一个来源文件。
        dict_sources[str_relative_path] = path_source  # 当前文件的内部读取路径

    # 校验角色确认的 shape、路径和值域。
    dict_confirmations = _validate_confirmations(path_root, spec, dict_sources)  # 规范化确认映射

    # formatter 报告按公开相对路径索引，避免绝对路径造成跨机器漂移。
    dict_reports = _index_formatter_reports(path_root, reports)  # `.v` 结构事实的可信来源

    # 文件事实按公开路径顺序收集，避免平台遍历顺序漂移。
    list_facts: list[VgFileFacts] = []  # 等待返回的有序文件事实

    # 每个文件只读取一次并保存确定性角色来源。
    for str_path, path_source in sorted(
        dict_sources.items(), key=lambda item: (item[0].casefold(), item[0])
    ):

        # 显式名称优先于目录证据，同时保留全部命中项。
        tuple_role_details = _explicit_role(str_path)  # 当前文件的显式角色三元组

        # 角色值供后续确认覆盖与 gate 评估使用。
        str_role = tuple_role_details[0]  # 当前文件角色

        # 来源值说明角色由名称、目录还是确认确定。
        str_role_source = tuple_role_details[1]  # 当前角色来源

        # 普通 design 也经过内容检查，公开来源不得落入枚举外的空值。
        if str_role_source == "":

            # 未命中显式测试证据表示内容检查维持 design 结论。
            str_role_source = "content_evidence"  # 普通 design 的确定性来源

        # 全量显式证据用于报告解释，不参与确认覆盖。
        tuple_evidence = tuple_role_details[2]  # 当前文件角色证据

        # 每个文件读取一次，供错误传播与内容证据共同消费。
        tuple_source_result = _read_source_result(path_source, str_path)  # 源码读取结果二元组

        # 第一项只在读取成功时保存完整源码。
        str_source = tuple_source_result[0]  # 当前文件的可选源码

        # 第二项只在读取失败时保存稳定诊断。
        str_read_error = tuple_source_result[1]  # 当前文件的可选读取错误

        # 非显式文件以保守词法事实判断是否需要角色确认。
        if str_role == "design" and str_source is not None:

            # 扩展名路由与报告补建由独立 helper 保持单一职责。
            tuple_content_evidence = _file_content_role_evidence(  # 当前文件的强证据组
                path_source,  # 当前文件的规范路径
                str_path,  # 当前文件的公开相对路径
                str_source,  # 已读取的当前源码
                dict_reports,  # 按公开路径索引的 formatter 报告
            )

            # 至少两个不同组才允许进入待确认状态。
            if len(tuple_content_evidence) >= 2:

                # 双证据使普通文件进入 ambiguous 状态。
                str_role = "ambiguous"  # 双证据文件的待确认角色

                # 角色来源明确记录为内容事实。
                str_role_source = "content_evidence"  # 内容扫描是当前角色来源

                # 全部不同证据组进入结构化报告。
                tuple_evidence = tuple_content_evidence  # 报告公开的确定性证据组

        # 仅接受经过全量校验的当前文件确认。
        str_confirmed_role = dict_confirmations.get(str_path)  # 当前文件的可选用户确认

        # 显式 testbench 不允许被 design 确认覆盖。
        if str_role == "testbench" and str_confirmed_role == "design":

            # 冲突确认必须 fail closed，不能弱化显式测试证据。
            raise ValueError("> ERR: [Python] file_role_confirmations 与显式 testbench 角色冲突")

        # 非显式文件可以由用户确认其真实角色。
        if str_confirmed_role is not None and str_role != "testbench":

            # 用户确认成为普通文件的最终角色。
            str_role = str_confirmed_role  # 当前文件经确认后的角色

            # 角色来源同步标记为人工确认。
            str_role_source = "confirmed"  # 当前文件经确认后的来源

        # 组装本文件的不可变公开事实。
        list_facts.append(
            VgFileFacts(
                path=str_path,  # 对外稳定的相对路径
                extension=path_source.suffix,  # 文件实际扩展名

                # 角色字段共同描述当前分类及其依据。
                role=str_role,  # 当前角色结论
                role_source=str_role_source,  # 判定该角色的证据来源
                role_evidence=tuple_evidence,  # 全部显式角色证据

                # 确认字段区分自动结论与人工输入。
                confirmation_required=str_role == "ambiguous",  # 未确认双证据文件需宿主询问
                confirmed_role=str_confirmed_role,  # 可选人工确认

                # 内外路径信息分别服务报告与内部读取。
                read_error=str_read_error,  # 可选稳定读取诊断
                source_path=path_source,  # 仅供内部读取的绝对路径
            )
        )

    # 返回不可变事实集合，防止规则间修改共享状态。
    return tuple(list_facts)

# evaluate_file_gate 统一执行 VG148 与当前最小 VG149 角色合同。
def evaluate_file_gate(gate_id: str, files: tuple[VgFileFacts, ...]) -> VgEvaluation:
    """根据 gate ID 评估文件事实。

    参数:
        gate_id: `VG148` 或 `VG149`。
        files: 已完成输入校验的文件事实集合。
    返回:
        与统一语义引擎兼容的执行结论。
    异常:
        ValueError: gate ID 不属于文件预检规则时抛出。
    """

    # 读取失败优先于任何命名通过结论。
    tuple_read_errors = tuple(  # 阻止不可读输入进入命名通过路径
        file_fact for file_fact in files if file_fact.read_error  # 仅保留读取失败项
    )

    # 任一不可读输入都使文件门禁返回 error。
    if tuple_read_errors:

        # 错误 finding 只公开相对路径和稳定诊断。
        tuple_findings = tuple(  # 每个不可读文件对应一个稳定错误 finding
            VgFinding(  # 构造统一模型可消费的错误证据
                path=file_fact.path,  # 失败文件的公开路径
                line=None,  # 读取失败没有可验证的源码行，保留文件级定位
                message="文件无法读取，命名与角色预检未完成。",  # 稳定错误说明
                evidence=file_fact.read_error or "读取失败",  # 不泄露绝对路径的证据
            )
            for file_fact in tuple_read_errors  # 逐个转换不可读文件
        )

        # error 状态阻止调用方把不可读输入伪装成 passed。
        return VgEvaluation("error", True, tuple_findings, "存在无法读取的文件")

    # VG148 独立检查全部已发现文件的终止后缀。
    if gate_id == "VG148":

        # 返回确定性的版本号或纯数字后缀结论。
        return _evaluate_vg148(files)

    # VG149 同时处理内容疑似与确定 testbench 命名。
    if gate_id == "VG149":

        # 返回显式或已确认 testbench 的命名结论。
        return _evaluate_vg149(files)

    # 未注册 ID 不能静默退化为不适用。
    raise ValueError("> ERR: [Python] 未知文件预检 gate ID")

# _discover_sources 统一单文件与目录发现语义。
def _discover_sources(path_root: Path) -> tuple[Path, ...]:
    """发现扫描根下所有受支持的 Verilog 文件。

    参数:
        path_root: 已解析的单文件或目录根。
    返回:
        按相对路径稳定排序的文件路径。
    异常:
        ValueError: 根不存在或单文件后缀不受支持时抛出。
    """

    # 不存在的扫描根没有可验证语义。
    if not path_root.exists():

        # 缺失输入必须显式阻断。
        raise ValueError("> ERR: [Python] 文件预检扫描根不存在")

    # 单文件入口只接受 .v 或 .sv，大小写不敏感。
    if path_root.is_file():

        # 不支持的单文件不能进入 Verilog 命名门禁。
        if path_root.suffix.casefold() not in VERILOG_FILE_EXTENSIONS:

            # 后缀越界必须 fail closed。
            raise ValueError("> ERR: [Python] 文件预检仅支持 .v 或 .sv")

        # 单文件返回一个规范绝对路径。
        return (path_root,)

    # 目录入口递归发现两种受支持后缀。
    list_sources = [  # 当前目录下全部 .v/.sv 文件
        path_source.resolve()  # 后续身份比较使用规范绝对路径
        for path_source in path_root.rglob("*")  # 递归遍历当前扫描目录
        if path_source.is_file()  # 目录项必须是普通文件
        and path_source.suffix.casefold() in VERILOG_FILE_EXTENSIONS  # 后缀必须受支持
    ]

    # 稳定排序同时处理大小写相同但原文不同的路径。
    list_sources.sort(
        key=lambda path_source: (
            path_source.relative_to(path_root).as_posix().casefold(),
            path_source.relative_to(path_root).as_posix(),
        )
    )

    # 返回不可变发现结果。
    return tuple(list_sources)

# _relative_path 统一单文件 basename 与目录 POSIX 路径。
def _relative_path(path_root: Path, path_source: Path) -> str:
    """生成跨机器稳定的公开相对路径。

    参数:
        path_root: 本轮扫描根。
        path_source: 当前发现文件。
    返回:
        单文件 basename 或目录下 POSIX 相对路径。
    """

    # 单文件扫描使用 basename 作为确认身份。
    if path_root.is_file():

        # 文件名不携带本机目录信息。
        return path_source.name

    # 目录扫描保留相对层级并统一为 POSIX 分隔符。
    return path_source.relative_to(path_root).as_posix()

# _validate_confirmations 拒绝陈旧、越界或冲突路径。
def _validate_confirmations(
    path_root: Path,
    spec: Mapping[str, Any] | None,
    sources: Mapping[str, Path],
) -> dict[str, str]:
    """校验并复制 file_role_confirmations 映射。

    参数:
        path_root: 本轮扫描根。
        spec: 可选调用规格。
        sources: 当前扫描发现的公开路径集合。
    返回:
        路径和值均已校验的独立字典。
    异常:
        ValueError: 映射 shape、路径或值域非法时抛出。
    """

    # 缺省规格等价于没有人工确认。
    dict_spec = dict(spec or {})  # 隔离调用方后续修改

    # 只读取冻结合同声明的确认字段。
    obj_confirmation_input: object = dict_spec.get(  # 待校验确认输入
        "file_role_confirmations", {}  # 缺省为空映射
    )

    # 确认容器必须是 JSON object 语义。
    if not isinstance(obj_confirmation_input, Mapping):

        # 非映射输入无法建立文件身份。
        raise ValueError("> ERR: [Python] file_role_confirmations 必须是映射")

    # 规范化结果不复用调用方可变容器。
    dict_confirmations: dict[str, str] = {}  # 已通过路径和值校验的确认

    # 通过 shape 校验后使用明确的只读映射类型。
    mapping_confirmation_source: Mapping[Any, Any] = obj_confirmation_input  # 待逐项验证的映射

    # 每个确认键都必须唯一指向当前扫描文件。
    for obj_key, obj_role in mapping_confirmation_source.items():

        # JSON 路径和值都必须是字符串。
        if not isinstance(obj_key, str) or not isinstance(obj_role, str):

            # 非字符串输入不能参与稳定路径比较。
            raise ValueError("> ERR: [Python] 文件角色确认的键和值必须是字符串")

        # 路径必须已经采用规范 POSIX 相对写法。
        str_path = _normalize_confirmation_path(obj_key)  # 当前确认的规范相对路径

        # 用户角色只允许 design 或 testbench。
        if obj_role not in {"design", "testbench"}:

            # 未知角色会污染后续 gate 状态。
            raise ValueError("> ERR: [Python] 文件角色确认值必须是 design 或 testbench")

        # 确认不得指向非 Verilog 文件。
        if PurePosixPath(str_path).suffix.casefold() not in VERILOG_FILE_EXTENSIONS:

            # 后缀越界说明确认不属于当前预检。
            raise ValueError("> ERR: [Python] 文件角色确认仅支持 .v 或 .sv")

        # 当前扫描不存在的路径视为陈旧确认。
        if str_path not in sources:

            # 陈旧或大小写错误的路径必须 fail closed。
            raise ValueError("> ERR: [Python] 文件角色确认未指向当前扫描文件")

        # 规范化后重复键不能依赖输入顺序覆盖。
        if str_path in dict_confirmations:

            # 重复身份可能携带相互冲突的角色。
            raise ValueError("> ERR: [Python] 文件角色确认路径规范化后重复")

        # 保存已经验证的当前文件角色。
        dict_confirmations[str_path] = obj_role  # 当前文件的已验证角色

    # 返回独立确认映射。
    return dict_confirmations

# _normalize_confirmation_path 执行跨平台词法安全检查。
def _normalize_confirmation_path(raw_path: str) -> str:
    """验证确认键是规范 POSIX 相对路径。

    参数:
        raw_path: 调用方提供的确认键。
    返回:
        规范化后保持原文的 POSIX 相对路径。
    异常:
        ValueError: 路径为空、绝对、越界或非规范时抛出。
    """

    # 空路径不能标识当前扫描中的文件。
    if not raw_path or "\\" in raw_path:

        # 反斜杠路径不是冻结合同要求的 POSIX 表示。
        raise ValueError("> ERR: [Python] 文件角色确认路径必须是非空 POSIX 相对路径")

    # 同时拒绝 POSIX 与 Windows 绝对路径。
    if PurePosixPath(raw_path).is_absolute() or PureWindowsPath(raw_path).is_absolute():

        # 绝对路径会泄露并绕过扫描根边界。
        raise ValueError("> ERR: [Python] 文件角色确认不得使用绝对路径")

    # 路径段必须保持规范，不能包含当前目录或父目录跳转。
    tuple_parts = PurePosixPath(raw_path).parts  # 当前确认键的词法路径段

    # 点段、空段与冒号都不属于规范相对身份。
    if any(str_part in {".", "..", ""} for str_part in tuple_parts) or ":" in raw_path:

        # 越界或非规范路径不能参与匹配。
        raise ValueError("> ERR: [Python] 文件角色确认路径包含非法段")

    # PurePosixPath 会折叠部分写法，必须要求调用方直接提供规范形式。
    str_normalized = PurePosixPath(raw_path).as_posix()  # 词法规范化相对路径

    # 原文与规范形式不同意味着存在重复身份风险。
    if str_normalized != raw_path:

        # 非规范键必须先由调用方修正。
        raise ValueError("> ERR: [Python] 文件角色确认路径必须预先规范化")

    # 返回可与公开文件 path 精确比较的键。
    return str_normalized

# _explicit_role 收集名称与目录中的显式测试语义。
def _explicit_role(relative_path: str) -> tuple[str, str, tuple[str, ...]]:
    """识别路径中的显式 testbench 角色证据。

    参数:
        relative_path: 当前文件的规范 POSIX 相对路径。
    返回:
        角色、主来源和全部显式证据。
    """

    # stem 与目录段按大小写不敏感方式判断。
    path_relative = PurePosixPath(relative_path)  # 当前文件的纯 POSIX 路径

    # 名称证据按冻结优先级收集。
    str_stem = path_relative.stem.casefold()  # 当前文件名主体

    # 证据集合保留稳定顺序并避免重复。
    list_evidence: list[str] = []  # 当前路径命中的显式测试证据

    # tb_ 前缀是唯一合规 testbench 命名形式。
    if str_stem.startswith("tb_"):

        # 记录唯一合规的测试平台名称前缀。
        list_evidence.append("name:tb_prefix")

    # _tb 后缀仍是角色证据，但会由 VG149 判为错名。
    if str_stem.endswith("_tb"):

        # 记录需要后续强制改名的测试平台后缀。
        list_evidence.append("name:tb_suffix")

    # testbench 子串是显式角色证据，不构成命名豁免。
    if "testbench" in str_stem:

        # 记录文件主体中明确出现的 testbench 语义。
        list_evidence.append("name:testbench")

    # 目录证据只接受冻结的精确段。
    tuple_directories = tuple(  # 当前文件上层目录的大小写无关名称
        str_part.casefold() for str_part in path_relative.parts[:-1]  # 目录名统一大小写
    )

    # 任一精确测试目录段都保留为角色证据。
    if any(str_part in TESTBENCH_DIRECTORY_NAMES for str_part in tuple_directories):

        # 目录证据与名称证据一起进入最终角色报告。
        list_evidence.append("directory:testbench")

    # 名称证据优先成为主来源。
    if any(str_item.startswith("name:") for str_item in list_evidence):

        # 显式名称已经确定当前文件是 testbench。
        return "testbench", "explicit_name", tuple(list_evidence)

    # 只有目录证据时使用独立来源值。
    if list_evidence:

        # 精确测试目录段确定 testbench 角色。
        return "testbench", "explicit_directory", tuple(list_evidence)

    # 普通文件没有角色来源，后续内容扫描只在双证据时填充来源。
    return "design", "", ()

# _read_source 提供可在测试中隔离的底层读取边界。
def _read_source(path_source: Path) -> str:
    """读取文件文本供角色证据扫描使用。

    参数:
        path_source: 当前 Verilog 或 SystemVerilog 文件。
    返回:
        UTF-8 源码文本。
    异常:
        OSError: 文件系统读取失败时原样传播给上层转换。
        UnicodeError: 输入不是有效 UTF-8 时传播给上层转换。
    """

    # 当前预检只读取文本，不在此处解析或修改内容。
    return path_source.read_text(encoding="utf-8")

# _read_source_result 把单次读取转换为源码或稳定相对诊断。
def _read_source_result(path_source: Path, relative_path: str) -> tuple[str | None, str | None]:
    """读取文件并返回源码与不泄露绝对路径的可选错误。

    参数:
        path_source: 当前内部绝对文件路径。
        relative_path: 当前公开相对路径。
    返回:
        成功时返回源码和 None，失败时返回 None 与稳定相对诊断。
    """

    # 捕获文件系统与编码读取失败，避免异常逃逸成伪通过。
    try:

        # 实际文本暂不保留，Task 2 将消费其内容证据。
        str_source = _read_source(path_source)  # 本文件唯一一次文本读取结果

    # 输入错误统一转换为公开相对路径诊断。
    except (OSError, UnicodeError) as exc:

        # 类型名足以区分失败种类，同时不复制异常内的绝对路径。
        str_error = f"{relative_path}: {type(exc).__name__}: 读取失败"  # 稳定相对诊断

        # 读取失败时不提供可能不完整的源码。
        return None, str_error

    # 读取成功时把完整文本交给内容证据扫描。
    return str_source, None

# _file_content_role_evidence 按扩展名选择可信内容事实。
def _file_content_role_evidence(
    path_source: Path,
    relative_path: str,
    source: str,
    reports: Mapping[str, Mapping[str, Any]],
) -> tuple[str, ...]:
    """为单个文件选择 formatter 或保守 lexer 内容证据。

    参数:
        path_source: 当前文件的规范路径。
        relative_path: 当前文件的公开相对路径。
        source: 已读取的当前源码。
        reports: 按公开路径索引的 formatter 报告。
    返回:
        按冻结顺序排列的内容证据组。
    """

    # SystemVerilog 不进入 Verilog-2001 formatter。
    if path_source.suffix.casefold() == ".sv":

        # 最小保守 lexer 负责 `.sv` 的全部四组证据。
        return _content_role_evidence(source)

    # `.v` 优先复用调用方已经提供的 formatter 报告。
    dict_report = reports.get(relative_path)  # 当前 `.v` 的可选既有报告

    # 独立 collector 入口只在报告缺席时补建一次。
    if dict_report is None:

        # 单文件 formatter 构建不会覆盖调用方已有报告。
        dict_report = build_ast_report_for_path(path_source)  # 当前 `.v` 的唯一补建报告

    # 结构报告与有限原文补充共同形成 `.v` 证据。
    return _verilog_content_role_evidence(dict_report, source)

# _index_formatter_reports 把调用方报告绑定到公开相对路径。
def _index_formatter_reports(
    path_root: Path,
    reports: Sequence[Mapping[str, Any]],
) -> dict[str, Mapping[str, Any]]:
    """按规范化扫描路径索引 formatter 报告。

    参数:
        path_root: 当前文件扫描根。
        reports: 调用方已经构建的 formatter 报告。
    返回:
        以公开 POSIX 相对路径为键的只读报告映射。
    """

    # 索引只保存能够稳定回指当前扫描根的报告。
    dict_reports: dict[str, Mapping[str, Any]] = {}  # 公开路径到 formatter 报告

    # 调用方顺序不影响查找，重复身份保持首个可信报告。
    for mapping_report in reports:

        # 单份报告的路径恢复与边界检查由 helper 隔离。
        str_relative_path = _formatter_report_relative_path(  # 当前报告的可选公开路径
            path_root,  # 当前扫描根
            mapping_report,  # 当前 formatter 报告
        )

        # 无法稳定绑定的报告不得进入可信索引。
        if str_relative_path is None:

            # 跳过畸形、非规范或扫描根外的报告。
            continue

        # 首个报告身份保持稳定，重复输入不依赖后项覆盖。
        dict_reports.setdefault(str_relative_path, mapping_report)

    # 返回独立索引，调用方报告对象本身保持只读使用。
    return dict_reports

# _formatter_report_relative_path 恢复单份报告的公开身份。
def _formatter_report_relative_path(
    path_root: Path,
    report: Mapping[str, Any],
) -> str | None:
    """把 formatter 报告路径恢复为当前扫描根下的 POSIX 路径。

    参数:
        path_root: 当前文件扫描根。
        report: 等待恢复身份的 formatter 报告。
    返回:
        可绑定时返回公开 POSIX 路径，否则返回 None。
    """

    # relative_path 是跨机器恢复身份的首选字段。
    obj_relative_path = report.get("relative_path")  # 可选公开相对路径

    # 已提供的相对路径复用确认键的严格规范检查。
    if isinstance(obj_relative_path, str) and obj_relative_path:

        # 非规范报告路径不能静默进入可信索引。
        try:

            # 规范路径直接作为报告索引键。
            return _normalize_confirmation_path(obj_relative_path)

        # 非规范相对路径不具备可信身份。
        except ValueError:

            # 畸形报告不会参与内容角色判断。
            return None

    # 缺少相对路径时从 formatter 的真实 path 字段恢复身份。
    obj_report_path = report.get("path")  # formatter 报告的文件路径

    # 无文件路径的报告无法绑定到当前扫描来源。
    if not isinstance(obj_report_path, str) or not obj_report_path:

        # 空身份不参与任何文件角色判断。
        return None

    # 相对 report path 以扫描根目录为基准。
    path_report = Path(obj_report_path)  # 当前报告声明的文件路径

    # 单文件扫描和目录扫描使用各自的稳定恢复规则。
    if path_report.is_absolute():

        # 绝对路径直接解析为 formatter 对应文件。
        path_source = path_report.resolve()  # formatter 对应的规范绝对路径

    # 单文件入口只允许绑定当前文件本身。
    elif path_root.is_file():

        # 报告身份收敛为当前单文件扫描根。
        path_source = path_root  # 单文件报告恒绑定当前扫描文件

    # 目录入口把相对报告路径约束在扫描根下。
    else:

        # 拼接后规范化，随后再执行越界检查。
        path_source = (path_root / path_report).resolve()  # 目录内相对报告路径

    # 扫描根外的报告必须被排除。
    try:

        # 恢复成功时返回与文件事实一致的公开身份。
        return _relative_path(path_root, path_source)

    # relative_to 失败表示报告指向扫描根外。
    except ValueError:

        # 越界报告不得参与任何文件角色判断。
        return None

# _verilog_content_role_evidence 合并 formatter 结构与原文补充事实。
def _verilog_content_role_evidence(
    report: Mapping[str, Any],
    source: str,
) -> tuple[str, ...]:
    """从 `.v` formatter 报告和有限原文事实提取角色证据。

    参数:
        report: 当前 `.v` 的 formatter AST 报告。
        source: 仅用于 formatter 尚未表达的仿真任务和时钟激励。
    返回:
        按冻结顺序排列的内容证据组。
    """

    # 结构证据只从 formatter 报告读取，不重新用正则解释 `.v` 语法。
    set_evidence: set[str] = set()  # 当前 `.v` 的结构与补充证据

    # modules 是 formatter 对 Verilog-2001 结构的可信边界。
    tuple_modules = report.get("modules", ())  # 报告中的模块事实集合

    # 只消费映射形式的模块事实，畸形项不会制造角色证据。
    if isinstance(tuple_modules, Sequence) and not isinstance(tuple_modules, (str, bytes)):

        # 每个模块分别检查 initial 与 DUT 实例结构。
        for obj_module in tuple_modules:

            # 非映射模块不参与结构判断。
            if not isinstance(obj_module, Mapping):

                # 跳过无法表达 formatter 结构的畸形模块项。
                continue

            # formatter 已确认的 initial 块构成第一组证据。
            if obj_module.get("initials"):

                # initial 结构只登记一次证据组。
                set_evidence.add("initial_process")

            # DUT 自检必须同时具备实例与结构化断言或比较事实。
            if obj_module.get("instances") and _report_has_self_check(obj_module):

                # 实例与自检控制共同登记 DUT 自检证据。
                set_evidence.add("dut_self_check")

    # 原文只补 formatter 尚未完整表达的 simulation task 与 clock stimulus。
    str_code = _mask_non_code_tokens(source)  # 保留代码布局的有限词法输入

    # 完整仿真系统任务调用贡献独立证据组。
    if re.search(
        r"\$(?:finish|stop|fatal|dumpfile|dumpvars)\b\s*(?:\([^;]*\))?\s*;",
        str_code,
        re.IGNORECASE,
    ):

        # 完整系统任务只登记一次仿真行为证据。
        set_evidence.add("simulation_task")

    # 延时反相或交替驱动仍由共享保守 helper 识别。
    if _has_clock_stimulus(str_code):

        # 完整延时驱动登记时钟激励证据。
        set_evidence.add("clock_stimulus")

    # 固定顺序同时稳定本地报告与远端 retained summary。
    return tuple(str_group for str_group in CONTENT_EVIDENCE_ORDER if str_group in set_evidence)

# _report_has_self_check 只识别 formatter 报告中的断言或比较事实。
def _report_has_self_check(value: object) -> bool:
    """递归判断 formatter 事实是否包含结构化自检控制。

    参数:
        value: 当前模块报告或其任意嵌套事实。
    返回:
        存在 assert 节点或相等比较操作时返回 True。
    """

    # 映射节点先检查自身语义，再递归检查子事实。
    if isinstance(value, Mapping):

        # formatter 的 kind 字段族可以稳定表达 assert 节点。
        tuple_kinds = (
            value.get("kind"),  # formatter 通用节点类型
            value.get("node_kind"),  # 表达式节点类型
            value.get("operation_kind"),  # 操作节点类型
        )  # 当前结构节点的可选类型标识

        # 任一明确 assert 类型都构成自检控制。
        if any(str(obj_kind).casefold() == "assert" for obj_kind in tuple_kinds if obj_kind is not None):

            # assert 节点直接提供结构化自检结论。
            return True

        # formatter 表达式事实中的相等比较表示期望值检查。
        if value.get("operator") in {"===", "!==", "==", "!="}:

            # 相等比较直接形成期望值检查。
            return True

        # formatter 控制节点的 header 保留已解析条件，可识别相等比较。
        str_header = str(value.get("header") or "")  # 当前结构节点的规范 header

        # 完整 if 条件中的相等比较构成结构化期望值检查。
        if re.search(r"\bif\s*\([^)]*(?:===|!==|==|!=)[^)]*\)", str_header, re.IGNORECASE):

            # formatter 控制 header 已明确表达比较条件。
            return True

        # formatter statement 节点中的错误任务代表明确失败路径。
        str_text = str(value.get("text") or "")  # 当前语句节点的规范文本

        # `$error` 或 `$fatal` 语句与 DUT 实例共同构成自检证据。
        if re.search(r"\$(?:error|fatal)\b\s*(?:\([^;]*\))?\s*;", str_text, re.IGNORECASE):

            # 明确失败语句提供自检失败路径。
            return True

        # 嵌套映射和值序列继续沿 formatter 事实树搜索。
        return any(_report_has_self_check(obj_child) for obj_child in value.values())

    # 字符串和字节不是结构子节点，不能贡献报告事实。
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):

        # 列表中的任一结构节点满足条件即可确定自检存在。
        return any(_report_has_self_check(obj_child) for obj_child in value)

    # 其他标量没有 formatter 自检语义。
    return False

# _content_role_evidence 从去除非代码 token 的文本提取四组保守证据。
def _content_role_evidence(str_source: str) -> tuple[str, ...]:
    """返回按冻结顺序排列的 testbench 内容证据组。

    参数:
        str_source: 当前 Verilog 或 SystemVerilog 源码。
    返回:
        去重后的强证据组元组。
    """

    # 注释和字符串不得贡献系统任务、自检或实例证据。
    str_code = _mask_non_code_tokens(str_source)  # 保留源码布局的纯代码文本

    # 每个组只保存一次，重复结构不增加阈值计数。
    set_evidence: set[str] = set()  # 当前文件命中的证据组

    # initial 必须带 begin 或完整单语句，孤立关键字不计。
    if re.search(r"\binitial\b\s*(?:begin\b|[^;\r\n]+;)", str_code, re.IGNORECASE):

        # 完整 initial 过程贡献第一组结构证据。
        set_evidence.add("initial_process")

    # 仿真系统任务必须形成完整分号结束的调用。
    if re.search(
        r"\$(?:finish|stop|fatal|dumpfile|dumpvars)\b\s*(?:\([^;]*\))?\s*;",
        str_code,
        re.IGNORECASE,
    ):

        # 完整系统任务调用贡献仿真行为证据。
        set_evidence.add("simulation_task")

    # 延时反相或固定值交替驱动同一信号构成时钟激励。
    if _has_clock_stimulus(str_code):

        # 保守时钟结构贡献独立激励证据。
        set_evidence.add("clock_stimulus")

    # DUT 自检要求实例化和完整检查控制同时存在。
    if _has_complete_instance(str_code) and _has_self_check(str_code):

        # 两类完整结构共同贡献 DUT 自检证据。
        set_evidence.add("dut_self_check")

    # 固定顺序保证 finding metadata 在不同平台一致。
    return tuple(str_group for str_group in CONTENT_EVIDENCE_ORDER if str_group in set_evidence)

# _mask_non_code_tokens 用状态机屏蔽注释与字符串但保留换行。
def _mask_non_code_tokens(str_source: str) -> str:
    """屏蔽 Verilog 注释和字符串并保留字符位置。

    参数:
        str_source: 待扫描的完整源码。
    返回:
        注释和字符串替换为空格、换行保持不变的文本。
    """

    # 输出与输入等长，便于后续按源码布局执行保守模式识别。
    list_masked: list[str] = []  # 已处理字符的等长屏蔽结果

    # 状态只覆盖代码、行注释、块注释和字符串四种词法环境。
    str_state = "code"  # 当前词法状态

    # 转义标志只在字符串状态内生效。
    bool_escaped = False  # 前一个字符串字符是否为反斜杠

    # 索引允许同时观察注释起始的两个字符。
    int_index = 0  # 当前待处理字符下标

    # 逐字符扫描避免正则跨越未闭合注释或转义字符串。
    while int_index < len(str_source):

        # 当前字符用于状态转移与输出。
        str_char = str_source[int_index]  # 当前源码字符

        # 后继字符用于识别双字符注释边界。
        str_next = str_source[int_index + 1] if int_index + 1 < len(str_source) else ""  # 后继字符

        # 代码状态识别三种非代码 token 起点。
        if str_state == "code":

            # 双斜杠进入行注释并同时屏蔽起始字符。
            if str_char == "/" and str_next == "/":

                # 两个注释起始字符都替换为空格。
                list_masked.extend((" ", " "))

                # 后续字符按行注释规则处理。
                str_state = "line_comment"  # 后续字符屏蔽到换行

                # 跳过已经成对消费的起始字符。
                int_index += 2  # 下一个尚未处理的字符位置

                # 当前 token 已完整处理。
                continue

            # 斜杠星号进入块注释并屏蔽起始字符。
            if str_char == "/" and str_next == "*":

                # 块注释开界符保持长度但清除词法含义。
                list_masked.extend((" ", " "))

                # 后续字符按块注释规则处理。
                str_state = "block_comment"  # 后续字符屏蔽到闭合符

                # 扫描游标越过块注释开界符。
                int_index += 2  # 块注释内容的首字符位置

                # 开界符不再进入本轮其他分支。
                continue

            # 双引号进入字符串并屏蔽定界符。
            if str_char == '"':

                # 字符串起始定界符替换为空格。
                list_masked.append(" ")

                # 后续字符按字符串转义规则处理。
                str_state = "string"  # 后续字符按转义规则处理

                # 新字符串起点没有前导转义。
                bool_escaped = False  # 当前字符未被反斜杠转义

                # 扫描游标进入字符串内容区域。
                int_index += 1  # 字符串首个内容字符位置

                # 当前定界符已完整处理。
                continue

            # 普通代码字符原样保留。
            list_masked.append(str_char)

            # 扫描游标前进一个普通代码字符。
            int_index += 1  # 下一源码字符位置

            # 当前代码字符不再进入其他状态分支。
            continue

        # 行注释只在换行处恢复代码状态。
        if str_state == "line_comment":

            # 注释内容替换为空格，但保留行结构。
            list_masked.append(str_char if str_char in "\r\n" else " ")

            # 换行本身属于源码布局而非注释内容。
            if str_char in "\r\n":

                # 行尾使后续字符重新具备代码语义。
                str_state = "code"  # 下一字符重新按代码解释

            # 扫描游标越过当前行注释字符。
            int_index += 1  # 行注释中的后续位置

            # 行注释字符不进入块注释或字符串分支。
            continue

        # 块注释闭合符需要成对消费。
        if str_state == "block_comment":

            # 星号斜杠结束当前块注释。
            if str_char == "*" and str_next == "/":

                # 两个闭合字符都替换为空格。
                list_masked.extend((" ", " "))

                # 闭合后恢复普通代码状态。
                str_state = "code"  # 闭合后恢复代码扫描

                # 扫描游标越过块注释闭合符。
                int_index += 2  # 闭合符之后的源码位置

                # 当前块注释闭合符已完整处理。
                continue

            # 块注释内部只保留换行。
            list_masked.append(str_char if str_char in "\r\n" else " ")

            # 扫描游标越过一个块注释内容字符。
            int_index += 1  # 块注释中的后续位置

            # 块注释字符不进入字符串分支。
            continue

        # 字符串内的转义字符不会结束字符串。
        list_masked.append(str_char if str_char in "\r\n" else " ")

        # 未转义双引号结束当前字符串。
        if str_char == '"' and not bool_escaped:

            # 闭合定界符使后续文本恢复代码语义。
            str_state = "code"  # 字符串闭合后恢复代码扫描

        # 连续反斜杠按奇偶关系更新转义状态。
        bool_escaped = str_char == "\\" and not bool_escaped  # 下一字符的转义判定状态

        # 扫描游标越过当前字符串字符。
        int_index += 1  # 字符串中的后续位置

    # 拼接后的文本保持原始长度与换行位置。
    return "".join(list_masked)

# _has_clock_stimulus 识别完整的延时时钟驱动结构。
def _has_clock_stimulus(str_code: str) -> bool:
    """判断纯代码文本是否包含保守时钟激励。

    参数:
        str_code: 已屏蔽注释与字符串的源码。
    返回:
        存在完整延时反相或固定值交替驱动时返回 True。
    """

    # 反相自驱动是常见且确定的 testbench 时钟写法。
    match_toggle = re.search(  # 可选的完整延时反相驱动
        r"#\s*(?:\d+|\([^)]*\))\s*([A-Za-z_]\w*)\s*(?:<=|=)\s*~\s*\1\s*;",  # 驱动结构
        str_code,  # 已去除非代码 token 的源码
        re.IGNORECASE,  # 标识符匹配不区分大小写
    )  # 可选的延时反相驱动

    # 命中完整反相语句即可确定激励组。
    if match_toggle is not None:

        # 反相自驱动直接满足时钟激励合同。
        return True

    # 固定值驱动按信号收集 0/1 两种完整赋值。
    dict_fixed_values: dict[str, set[str]] = {}  # 候选时钟及其延时驱动值

    # 每个匹配都必须包含延时、赋值目标、常量和分号。
    for match_fixed in re.finditer(
        r"#\s*(?:\d+|\([^)]*\))\s*([A-Za-z_]\w*)\s*(?:<=|=)\s*(?:1\s*'\s*b)?([01])\s*;",
        str_code,
        re.IGNORECASE,
    ):

        # 目标信号名统一大小写后参与分组。
        str_signal = match_fixed.group(1).casefold()  # 当前候选驱动信号

        # 捕获的单比特常量用于判断交替驱动。
        str_value = match_fixed.group(2)  # 当前固定逻辑值

        # 将本次固定值合并到对应信号集合。
        dict_fixed_values.setdefault(str_signal, set()).add(str_value)

    # 同一信号同时出现 0 和 1 才视为交替时钟刺激。
    return any(set_values == {"0", "1"} for set_values in dict_fixed_values.values())

# _has_complete_instance 识别以分号闭合的模块实例化结构。
def _has_complete_instance(str_code: str) -> bool:
    """判断纯代码文本是否包含完整模块实例化。

    参数:
        str_code: 已屏蔽注释与字符串的源码。
    返回:
        存在模块名、实例名、端口括号和分号时返回 True。
    """

    # 关键字排除避免把 module、task 或控制语句误判为实例。
    str_pattern = (
        r"(?m)^\s*(?!(?:module|endmodule|task|function|if|for|while|case|assert)\b)"
        r"[A-Za-z_]\w*\s*(?:#\s*\([^;]*?\)\s*)?[A-Za-z_]\w*\s*\([^;]*\)\s*;"
    )  # 完整实例化的保守行首模式

    # 搜索结果只表达完整语法外形，不尝试解析一般 SystemVerilog。
    return re.search(str_pattern, str_code, re.IGNORECASE) is not None

# _has_self_check 识别完整错误、断言、比较或 pass/fail 控制。
def _has_self_check(str_code: str) -> bool:
    """判断纯代码文本是否包含完整自检控制。

    参数:
        str_code: 已屏蔽注释与字符串的源码。
    返回:
        存在完整自检语句时返回 True。
    """

    # 系统错误任务必须形成完整调用语句。
    if re.search(r"\$error\b\s*(?:\([^;]*\))?\s*;", str_code, re.IGNORECASE):

        # 完整错误调用直接提供自检失败路径。
        return True

    # 立即断言必须同时具有条件括号与语句结束。
    if re.search(r"\bassert\s*\([^;]+\)\s*(?:else\b[^;]*)?;", str_code, re.IGNORECASE):

        # 完整立即断言直接提供自检判定。
        return True

    # 条件表达式中的相等比较表示期望值检查。
    if re.search(r"\bif\s*\([^)]*(?:===|!==|==|!=)[^)]*\)", str_code, re.IGNORECASE):

        # 完整条件比较足以形成自检控制。
        return True

    # 明确 pass/fail 标识符控制也属于自检结果。
    return re.search(r"\b(?:pass|fail)(?:ed)?\b\s*(?:<=|=)", str_code, re.IGNORECASE) is not None

# _evaluate_vg148 只拒绝终止独立版本或数字段。
def _evaluate_vg148(files: tuple[VgFileFacts, ...]) -> VgEvaluation:
    """执行 VG148 文件名后缀检查。

    参数:
        files: 已读取的文件事实集合。
    返回:
        失败 finding 或全部文件通过结论。
    """

    # 命中项按文件事实顺序输出，保持报告确定性。
    list_findings: list[VgFinding] = []  # 当前 VG148 违规集合

    # 每个文件的 stem 独立执行锚定检查。
    for file_fact in files:

        # 当前主体不含扩展名，避免扩展中的字符参与数字后缀匹配。
        str_stem = PurePosixPath(file_fact.path).stem  # 用于终止后缀匹配的原始名称

        # 只接受终止段，不扫描 token 内部数字。
        match_suffix = INVALID_SUFFIX_PATTERN.search(str_stem)  # 可选违规末尾段

        # 合法功能命名无需生成 finding。
        if match_suffix is None:

            # 继续检查下一个文件。
            continue

        # 版本关键字决定 finding 分类。
        str_suffix_kind = (  # 当前命中段的稳定分类
            "version_suffix"  # 带版本关键字的独立后缀
            if match_suffix.group("version_kind")  # 关键字捕获决定分类
            else "numeric_suffix"  # 纯数字独立后缀
        )

        # 建议保留功能前缀并要求补充真实功能词。
        str_base = str_stem[: match_suffix.start()]  # 移除违规后缀后的功能前缀

        # 建议文本保留原扩展名，但不执行任何重命名。
        str_suggested_name = f"{str_base}_功能{file_fact.extension}"  # 真实功能命名占位建议

        # finding metadata 提供机器可读的命中细节。
        list_findings.append(
            VgFinding(
                path=file_fact.path,
                line=None,
                message="文件名末尾包含版本号或无功能含义的独立数字段。",
                evidence=match_suffix.group("matched_suffix"),
                metadata=(
                    ("matched_suffix", match_suffix.group("matched_suffix")),
                    ("suffix_kind", str_suffix_kind),
                    ("suggested_name", str_suggested_name),
                ),
            )
        )

    # 任一命中都构成 BLOCKER 失败。
    if list_findings:

        # 保留发现顺序交给统一报告层。
        return failed(*list_findings)

    # 已发现文件均完成检查时标记规则适用。
    return passed(applicable=bool(files))

# _evaluate_vg149 闭合内容疑似和确定 testbench 命名。
def _evaluate_vg149(files: tuple[VgFileFacts, ...]) -> VgEvaluation:
    """检查待确认角色以及 testbench 是否采用非空 tb_ 前缀。

    参数:
        files: 已读取的文件事实集合。
    返回:
        待确认、错误命名失败或当前确定角色通过结论。
    """

    # 内容双证据但尚未确认的文件必须由宿主询问用户。
    tuple_ambiguous = tuple(  # 当前所有待确认文件
        file_fact for file_fact in files if file_fact.role == "ambiguous"  # 只保留双证据角色
    )

    # 任一待确认角色都阻断严格交付。
    if tuple_ambiguous:

        # 可变集合逐项聚合，确保所有待确认文件都进入结论。
        list_ambiguous_findings: list[VgFinding] = []  # VG149 文件角色确认请求

        # 文件事实已有稳定顺序，直接沿用到 finding 输出。
        for file_fact in tuple_ambiguous:

            # 每个待确认文件生成独立且可展示的结构化事实。
            list_ambiguous_findings.append(
                VgFinding(
                    path=file_fact.path,  # 待确认文件的公开路径
                    line=None,  # 角色事实没有单一源码行，保留文件级定位
                    message="文件内容疑似 testbench，需要用户二次确认。",  # 面向宿主的询问原因
                    evidence=",".join(file_fact.role_evidence),  # 命中的不同证据组
                    metadata=(  # 宿主二次确认所需的结构化上下文
                        ("role", file_fact.role),  # 当前 ambiguous 角色
                        ("role_source", file_fact.role_source),  # content_evidence 来源
                        ("role_evidence", list(file_fact.role_evidence)),  # 稳定证据组列表
                        ("confirmation_required", True),  # 宿主必须二次确认
                        ("confirmed_role", file_fact.confirmed_role),  # 当前尚无确认
                    ),
                )
            )

        # inconclusive 明确区分待确认与确定命名违规。
        return inconclusive("存在内容疑似 testbench 的普通命名文件。", *list_ambiguous_findings)

    # 只对已识别为 testbench 的文件执行命名要求。
    tuple_testbenches = tuple(  # 已由名称、目录或确认识别的测试平台
        file_fact for file_fact in files if file_fact.role == "testbench"  # 测试平台角色项
    )

    # 普通设计文件在概念上不适用 VG149。
    if not tuple_testbenches:

        # 使用现有 passed/applicable=false 线协议。
        return passed(applicable=False)

    # 错误 testbench 名称按稳定文件顺序收集。
    list_findings: list[VgFinding] = []  # 当前 VG149 显式错名集合

    # 每个 testbench 必须具有非空功能名。
    for file_fact in tuple_testbenches:

        # 当前主体保留原始大小写，用于报告实际测试平台名称。
        str_stem = PurePosixPath(file_fact.path).stem  # 用于检查 tb_ 前缀的原始名称

        # 合规名称必须以 tb_ 开头且后面至少有一个字符。
        if str_stem.casefold().startswith("tb_") and len(str_stem) > 3:

            # 当前 testbench 已满足强制前缀合同。
            continue

        # finding 保留角色来源和确认状态供宿主展示。
        list_findings.append(
            VgFinding(
                path=file_fact.path,
                line=None,
                message="测试平台文件必须使用非空 tb_<功能名> 命名。",
                evidence=str_stem,
                metadata=(
                    ("role", file_fact.role),
                    ("role_source", file_fact.role_source),
                    ("role_evidence", list(file_fact.role_evidence)),
                    ("confirmation_required", file_fact.confirmation_required),
                    ("confirmed_role", file_fact.confirmed_role),
                ),
            )
        )

    # 错误名称必须失败，确认不能形成豁免。
    if list_findings:

        # 返回全部显式 testbench 错名证据。
        return failed(*list_findings)

    # 至少一个 testbench 且全部命名合规。
    return passed(applicable=True)
