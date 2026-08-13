"""Verilog facade 的 representative corpus 运行入口。"""

# future annotations 避免注解在导入期求值。
from __future__ import annotations

# json 用于把阶段状态和摘要稳定写成 UTF-8 文本。
import json

# Path 负责统一 representative 样例与运行目录路径。
from pathlib import Path

# Any 用于兼容旧 facade 的自由配置字典。
from typing import Any

# formatter AST 负责读取 RTL 文本并保留源编码信息。
from scripts.python.quality.formatter_ast import read_verilog_source

# formatter backend 负责生成 governed RTL 副本。
from scripts.python.quality.formatter_config import create_formatter_backend

# existing-RTL verify-repair 用于 representative 坏例验证。
from .existing_rtl_api import verify_existing_verilog

# quality gate 负责 governed RTL 的 strict 质量检查。
from .quality_api import check_verilog_quality

# workflow facade 的兼容配置合并逻辑在 run-cases 中复用。
from .workflow_api import _merged_option_dict

# 仓库根路径用于定位 tests/cases 与默认 runs 合同。
REPO_ROOT = Path(__file__).resolve().parents[5]  # 当前 skill 仓库根目录

# representative 坏例文本保持稳定顺序，便于 run-cases 结果对比。
str_bad_case_ids_text = (
    "missing_reset_fail bad_instance_shape_fail always_split_case_fail "
    "complex_case_slice_fail part_select_split_fail"
)  # representative 坏例的稳定 case_id 文本

# 把坏例文本切分成公共常量，供目录构造和顺序控制复用。
REPRESENTATIVE_BAD_CASE_IDS = tuple(str_bad_case_ids_text.split())  # representative 坏例 case_id 顺序

# representative 理想样例文本保持稳定顺序，便于治理回归比对。
str_ideal_case_ids_text = (
    "Sync_Reset_Interface Core_Tim_Ctrl_Interface Core_Ptp_Ctrl_Interface "
    "AXIS_ClockConverter_Interface AXIS_Buffer_Interface"
)  # representative 理想样例的稳定 case_id 文本

# 把理想样例序列固化为常量，便于回归时锁定好样例顺序。
REPRESENTATIVE_IDEAL_CASE_IDS = tuple(str_ideal_case_ids_text.split())  # representative 理想样例 case_id 顺序

# representative run-cases 的默认输出目录保持旧合同不变。
DEFAULT_CASE_RUN_DIR = Path("runs") / "representative-10"  # representative corpus 的默认运行目录

# _case_spec 把单个 case 元信息封装成稳定字典结构。
def _case_spec(str_case_id: str, str_cohort: str, path_source: Path) -> dict[str, Any]:
    """构造单个 representative case 的元信息字典。

    参数:
        str_case_id: representative case 的稳定标识。
        str_cohort: 当前样例所属的 cohort，例如 bad 或 ideal。
        path_source: 当前样例对应的源 RTL 文件路径。

    返回:
        返回包含 case_id、cohort 和 source 三个字段的元信息字典。
    """

    # 返回统一字典结构，便于后续目录选择和摘要序列化复用。
    return {
        "case_id": str_case_id,  # representative case 的稳定标识
        "cohort": str_cohort,  # 当前样例所属的 cohort
        "source": path_source,  # 当前样例对应的源 RTL 路径
    }

# _build_case_catalog 构造 case_id 到元信息的稳定映射。
def _build_case_catalog() -> dict[str, dict[str, Any]]:
    """构造 representative corpus 的 case_id 元信息索引。

    参数:
        无额外业务参数；函数直接读取模块级 case_id 常量。

    返回:
        返回按 case_id 建立索引的 representative 元信息字典。
    """

    # 目录字典负责承接 bad 与 ideal 两个 cohort 的统一索引。
    dict_case_catalog: dict[str, dict[str, Any]] = {}  # 按 case_id 建立索引的 representative 目录

    # 先登记 bad cohort，确保失败样例始终按固定顺序出现。
    for str_case_id in REPRESENTATIVE_BAD_CASE_IDS:

        # 当前坏例的源 RTL 固定落在 tests/cases/bad/rtl/fixtures。
        path_bad_case_source = REPO_ROOT / "tests" / "cases" / "bad" / "rtl" / "fixtures" / f"{str_case_id}.v"  # 当前坏例的源 RTL 文件路径

        # 把坏例登记进目录映射，便于后续按 case_id 直接取样例元信息。
        dict_case_catalog[str_case_id] = _case_spec(str_case_id, "bad", path_bad_case_source)  # 当前坏例的 representative 元信息

    # 再登记 ideal cohort，保持好样例顺序与既有合同一致。
    for str_case_id in REPRESENTATIVE_IDEAL_CASE_IDS:

        # 当前理想样例的 RTL 固定落在 tests/cases/ideal/rtl。
        path_ideal_case_source = REPO_ROOT / "tests" / "cases" / "ideal" / "rtl" / f"{str_case_id}.v"  # 当前理想样例的源 RTL 文件路径

        # 把理想样例登记进目录映射，便于后续保持好样例执行顺序稳定。
        dict_case_catalog[str_case_id] = _case_spec(str_case_id, "ideal", path_ideal_case_source)  # 当前理想样例的 representative 元信息

    # 返回按 case_id 建立索引的 representative 元信息目录。
    return dict_case_catalog

# representative 目录在导入期生成，主要服务 case_id 到样例元信息的快速查询。
REPRESENTATIVE_CASE_CATALOG = _build_case_catalog()  # 供 run-cases 按 case_id 快速查询样例元信息的目录表

# representative 默认执行顺序沿用坏例在前、理想样例在后的历史合同。
REPRESENTATIVE_CASE_ORDER = REPRESENTATIVE_BAD_CASE_IDS + REPRESENTATIVE_IDEAL_CASE_IDS  # representative corpus 的稳定执行顺序

# 公共导出名称文本保持 facade 稳定接口合同。
str_public_names_text = (
    "DEFAULT_CASE_RUN_DIR REPRESENTATIVE_BAD_CASE_IDS "
    "REPRESENTATIVE_CASE_CATALOG REPRESENTATIVE_CASE_ORDER "
    "REPRESENTATIVE_IDEAL_CASE_IDS run_verilog_cases"
)  # facade 公共导出名称文本

# 把公共导出名称文本切分成 __all__ 所需列表。
__all__ = str_public_names_text.split()  # representative facade 对外公开的符号列表

# _selected_representative_cases 把调用方 case 选择解析成元信息列表。
def _selected_representative_cases(raw_case_selection: Any) -> list[dict[str, Any]]:
    """把 run-cases 的 case 选择解析成 representative 元信息列表。

    参数:
        raw_case_selection: 调用方传入的 case 选择，可以是 None、字符串或字符串列表。

    返回:
        返回按请求顺序去重后的 representative 元信息列表。

    异常:
        ValueError: 当 case 选择为空或包含未知 case_id 时抛出。
    """

    # None 表示运行全部 representative 样例，并沿用稳定默认顺序。
    if raw_case_selection is None:

        # 复制默认顺序列表，避免调用方后续修改模块级常量。
        list_requested_case_ids = list(REPRESENTATIVE_CASE_ORDER)  # 本次请求的 representative case_id 列表

    # 单字符串输入表示只运行一个 case。
    elif isinstance(raw_case_selection, str):

        # 单 case 输入也统一包装成列表，便于后续去重和校验。
        list_requested_case_ids = [raw_case_selection]  # 单 case 输入对应的 case_id 列表

    # 列表和元组输入表示显式指定执行序列。
    elif isinstance(raw_case_selection, (list, tuple)):

        # 所有 case_id 都先转成字符串，保持旧接口宽松输入行为。
        list_requested_case_ids = [str(item) for item in raw_case_selection]  # 调用方显式指定的 case_id 列表

    # 其他输入类型不属于受支持的 case 选择合同。
    else:

        # 直接抛出输入合同错误，阻止未知类型继续进入 run-cases。
        raise ValueError(
            "> ERR: [Python] run_verilog_cases expects `case` to be None, a string, "
            "or a list of strings.",
        )

    # 空选择列表无法构成任何 representative 执行计划。
    if not list_requested_case_ids:

        # 阻止空选择进入下游，避免生成无意义的 run 目录。
        raise ValueError("> ERR: [Python] run_verilog_cases received an empty case selection.")

    # 已知 case_id 集合负责校验调用方输入是否合法。
    set_known_case_ids = set(REPRESENTATIVE_CASE_CATALOG)  # 当前 representative 目录中的全部合法 case_id

    # 去重后的顺序列表保持调用方第一次出现时的稳定顺序。
    list_unique_case_ids: list[str] = []  # 去重后的 representative case_id 列表

    # seen 集合用于识别重复 case_id，避免同一样例被重复执行。
    set_seen_case_ids: set[str] = set()  # 已经登记过的 representative case_id 集合

    # 逐项去重，保持调用方第一次出现时的稳定顺序。
    for str_case_id in list_requested_case_ids:

        # 已经登记过的 case 不再重复加入执行计划。
        if str_case_id in set_seen_case_ids:

            # 跳过重复样例，保持执行计划只含唯一 case。
            continue

        # 首次出现的 case_id 需要进入最终执行列表。
        list_unique_case_ids.append(str_case_id)

        # 记录已经登记过的 case_id，供后续重复检测复用。
        set_seen_case_ids.add(str_case_id)

    # 未知 case_id 会在这里统一收集，便于一次性向用户报告。
    list_unknown_case_ids: list[str] = []  # 本次请求里无法在 representative 目录找到的 case_id 列表

    # 再扫一遍去重后的请求列表，单独收集不存在于目录中的 case_id。
    for str_case_id in list_unique_case_ids:

        # 只有目录里不存在的样例才需要进入未知列表。
        if str_case_id not in set_known_case_ids:

            # 记录无法识别的 case_id，便于后续一次性报错。
            list_unknown_case_ids.append(str_case_id)

    # 只要存在未知 case_id，就不能继续进入实际执行阶段。
    if list_unknown_case_ids:

        # 可用样例文本用于提示调用方当前支持的 representative case 范围。
        str_available_cases = ", ".join(REPRESENTATIVE_CASE_ORDER)  # representative 目录支持的全部 case_id 文本

        # 未知样例文本用于把错误原因稳定呈现给调用方。
        str_unknown_cases = ", ".join(list_unknown_case_ids)  # 本次请求中无法识别的 case_id 文本

        # 把未知样例和可用样例一起返回，便于调用方立即修正输入。
        raise ValueError(
            f"> ERR: [Python] Unknown representative case(s): {str_unknown_cases}. "
            f"Available cases: {str_available_cases}.",
        )

    # 返回去重后的 representative 元信息列表，供实际执行阶段消费。
    return [dict(REPRESENTATIVE_CASE_CATALOG[str_case_id]) for str_case_id in list_unique_case_ids]

# _case_artifact_paths 生成单个 case 的固定产物路径合同。
def _case_artifact_paths(path_case_dir: Path, path_source_reference: Path) -> dict[str, Path]:
    """生成单个 representative case 的固定工件路径集合。

    参数:
        path_case_dir: 当前 case 的独立运行目录。
        path_source_reference: 当前 case 的源 RTL 参考路径。

    返回:
        返回包含 source、governed、报告和 verify 目录的路径字典。
    """

    # 源文件后缀优先沿用参考 RTL 的原始后缀，缺省时退回 .v。
    str_case_suffix = path_source_reference.suffix or ".v"  # 当前 case 源文件副本使用的 RTL 后缀

    # 返回单 case 运行期间全部固定产物路径。
    return {
        "source": path_case_dir / f"source{str_case_suffix}",  # 原始样例副本路径
        "governed": path_case_dir / f"governed{str_case_suffix}",  # governed RTL 副本路径
        "stage_status": path_case_dir / "stage_status.json",  # 单 case 阶段状态报告路径
        "strict_report_json": path_case_dir / "strict_report.json",  # strict 质量门 JSON 报告路径
        "strict_report_md": path_case_dir / "strict_report.md",  # 供人工查看 strict 结论的 Markdown 报告路径
        "verify_existing_dir": path_case_dir / "verify_existing",  # verify-existing 运行目录
    }

# _write_json 把字典稳定写成 UTF-8 JSON 文件。
def _write_json(path_output: Path, dict_payload: dict[str, Any]) -> None:
    """把结构化字典稳定写入 UTF-8 JSON 文件。

    参数:
        path_output: 目标 JSON 文件路径。
        dict_payload: 待序列化的字典载荷。

    返回:
        无业务返回值；结果直接落盘到 path_output。
    """

    # 父目录在写文件前必须存在，避免 JSON 落盘因目录缺失失败。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 把结构化字典稳定写成 UTF-8 JSON 文本，并保留换行结尾。
    path_output.write_text(
        json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

# _quality_issue_count 统计 strict 质量门指定严重级别的发现项数量。
def _quality_issue_count(dict_report: dict[str, Any], str_severity: str) -> int:
    """统计质量门报告中指定严重级别的发现项数量。

    参数:
        dict_report: 质量门返回的报告字典。
        str_severity: 待统计的 issue 严重级别名称。

    返回:
        返回匹配指定严重级别的 issue 数量。
    """

    # issue 列表只在报告字典形状正确时才继续统计。
    list_issues = dict_report.get("issues", []) if isinstance(dict_report, dict) else []  # 当前质量门报告中的 issue 列表

    # 返回匹配指定严重级别的 issue 数量。
    return sum(
        1
        for dict_issue in list_issues
        if isinstance(dict_issue, dict) and dict_issue.get("severity") == str_severity
    )

# _strict_quality_stage_payload 生成 strict 质量门阶段的稳定摘要字典。
def _strict_quality_stage_payload(
    bool_strict_ok: bool,
    dict_strict_report: dict[str, Any],
    path_report_json: Path,
    path_report_md: Path,
) -> dict[str, Any]:
    """生成 strict 质量门阶段的稳定摘要字典。

    参数:
        bool_strict_ok: 当前 strict 质量门是否通过。
        dict_strict_report: strict 质量门返回的完整报告字典。
        path_report_json: strict JSON 报告的落盘路径。
        path_report_md: strict Markdown 报告的落盘路径。

    返回:
        返回供 stage_status 记录的 strict 阶段摘要字典。
    """

    # 返回供 stage_status 持久化的 strict 质量门阶段摘要。
    return {
        "status": "passed" if bool_strict_ok else "reported",  # 当前 strict 阶段对外呈现的状态文本
        "profile": "formatter-normalize",  # 本轮 strict 检查实际使用的 formatter profile
        "ok": bool_strict_ok,  # strict 质量门是否满足通过条件
        "errors": _quality_issue_count(dict_strict_report, "error"),  # strict 报告里的 error 级问题数量
        "warnings": _quality_issue_count(dict_strict_report, "warning"),  # strict 报告里需要人工关注的 warning 数量
        "report_json": str(path_report_json),  # 自动化读取的 strict JSON 报告路径
        "report_md": str(path_report_md),  # 人工审阅的 strict Markdown 报告路径
    }

# _existing_path_str 把已存在工件路径转换为摘要可消费的字符串。
def _existing_path_str(path_value: Path) -> str | None:
    """把已存在的文件或目录路径转换成可选字符串。

    参数:
        path_value: 待检查的文件或目录路径。

    返回:
        如果路径已经存在则返回其字符串形式，否则返回 None。
    """

    # 已存在的路径需要写进摘要，便于上层直接定位工件。
    if path_value.exists():

        # 返回可直接放入 JSON 摘要的稳定字符串路径。
        return str(path_value)

    # 缺失工件在摘要中保持 None，表示当前产物尚未生成。
    return None

# _case_summary_payload 把单个 case 的运行结果裁剪成稳定摘要字段。
def _case_summary_payload(
    dict_case_spec: dict[str, Any],
    dict_case_paths: dict[str, Path],
    dict_stage_status: dict[str, Any],
    bool_strict_ok: bool,
    str_verify_status: str | None,
) -> dict[str, Any]:
    """把单个 representative case 的运行结果裁剪成稳定摘要字段。

    参数:
        dict_case_spec: 当前 representative case 的元信息字典。
        dict_case_paths: 当前 case 的固定工件路径字典。
        dict_stage_status: 当前 case 的阶段状态字典。
        bool_strict_ok: 当前 case 的 strict 质量门是否通过。
        str_verify_status: 当前 case 的 verify-existing 结果状态文本。

    返回:
        返回供 summary.json 和 summary.md 复用的稳定摘要字典。
    """

    # source 副本路径只在文件真实存在时才写进摘要。
    str_source_path = _existing_path_str(dict_case_paths["source"])  # source 副本的可选字符串路径

    # governed RTL 路径只在 formatter-preserve 真实落盘后才写进摘要。
    str_governed_path = _existing_path_str(dict_case_paths["governed"])  # governed RTL 的可选字符串路径

    # strict JSON 报告只在质量门真实写出后才回填到摘要中。
    str_strict_report_json = _existing_path_str(dict_case_paths["strict_report_json"])  # strict JSON 报告的可选字符串路径

    # strict Markdown 报告只在人工审阅文件真实写出后才回填到摘要中。
    str_strict_report_md = _existing_path_str(dict_case_paths["strict_report_md"])  # 供人工回看 strict 细节的 Markdown 报告路径

    # verify-existing 目录存在时要把目录位置回填进单 case 摘要。
    if dict_case_paths["verify_existing_dir"].is_dir():

        # 目录已生成时把路径转成摘要可直接展示的字符串。
        str_verify_existing_dir = str(dict_case_paths["verify_existing_dir"])  # verify-existing 工件目录的字符串路径

    # verify-existing 没有执行或没有生成目录时，摘要显式保持空值。
    else:

        # 用 None 明确表达 verify-existing 工件目录当前不存在。
        str_verify_existing_dir = None  # verify-existing 工件目录尚未生成

    # 返回供全局 summary 复用的稳定单 case 摘要结构。
    return {
        "case_id": str(dict_case_spec["case_id"]),  # summary 中用于唯一标识样例的 case_id
        "cohort": str(dict_case_spec["cohort"]),  # summary 中用于区分坏例和理想样例的 cohort
        "status": str(dict_stage_status["status"]),  # 单 case 主流程最终落下的完成状态
        "source_path": str_source_path,  # 复制后的 source RTL 文件路径，缺失时为空
        "governed_path": str_governed_path,  # formatter-preserve 生成的 governed RTL 路径，缺失时为空
        "stage_status_path": str(dict_case_paths["stage_status"]),  # 可直接回放阶段细节的 stage_status 文件路径
        "strict_ok": bool_strict_ok,  # strict 质量门对当前样例给出的通过标记
        "strict_report_json": str_strict_report_json,  # 自动化消费的 strict JSON 报告路径，缺失时为空
        "strict_report_md": str_strict_report_md,  # 人工查看的 strict Markdown 报告路径，缺失时为空
        "verify_existing_status": str_verify_status,  # verify-existing 阶段汇报的状态文本
        "verify_existing_dir": str_verify_existing_dir,  # verify-existing 工件目录路径，未生成时为空
    }

# _run_single_representative_case 负责执行一个 representative case。
def _run_single_representative_case(
    dict_case_spec: dict[str, Any],
    path_case_dir: Path,
) -> dict[str, Any]:
    """执行单个 representative case，并写出固定合同要求的产物。

    参数:
        dict_case_spec: 当前 representative case 的元信息字典。
        path_case_dir: 当前 case 的独立运行目录。

    返回:
        返回当前 case 的稳定摘要字典。

    异常:
        ValueError: 当代表样例源 RTL 缺失时抛出，并在 stage_status 中记录失败信息。
    """

    # 源参考路径在进入执行逻辑前先归一化成 Path。
    path_source_reference = Path(dict_case_spec["source"])  # 当前 representative 样例的参考 RTL 路径

    # 当前 case 的固定工件路径在执行前一次性展开。
    dict_case_paths = _case_artifact_paths(path_case_dir, path_source_reference)  # 当前 case 的固定工件路径字典

    # strict 质量门结果默认视为未通过，只有执行成功后才覆盖。
    bool_strict_ok = False  # 当前 case 的 strict 质量门是否通过

    # verify-existing 结果默认留空，只有坏例路径真正跑完才回填状态。
    str_verify_status: str | None = None  # 当前 case 的 verify-existing 状态文本

    # 单 case 阶段状态从 running 起步，便于异常时仍能留下轨迹。
    dict_stage_status = {
        "case_id": dict_case_spec["case_id"],  # 供阶段回放重新定位样例的 stable case_id
        "cohort": dict_case_spec["cohort"],  # 标明当前样例属于 bad 还是 ideal cohort
        "source_reference": str(path_source_reference),  # 记录 tests/cases 下原始 RTL 参考路径
        "status": "running",  # 当前单 case 主流程开始执行时的阶段状态
        "stages": {},  # 单 case 内部 copy、quality 与 verify 的阶段结果容器
    }  # 当前 representative case 的阶段状态字典

    # stages 子字典集中承接 copy、quality 和 verify 的分阶段结果。
    dict_stages = dict_stage_status["stages"]  # 当前 case 的分阶段执行记录字典

    # 单 case 的主流程使用 try/finally，确保 stage_status 始终落盘。
    try:

        # 当前 case 目录需要在执行前就创建，避免后续工件写出失败。
        path_case_dir.mkdir(parents=True, exist_ok=True)

        # 缺失源 RTL 时不能继续运行 representative case。
        if not path_source_reference.is_file():

            # 直接抛出缺失源文件错误，阻止无效 case 继续执行。
            raise ValueError(
                f"> ERR: [Python] Representative case source is missing: {path_source_reference}",
            )

        # source 副本路径会被后续 governed RTL 和报告阶段反复复用。
        path_case_source = dict_case_paths["source"]  # 当前 case 的 source RTL 副本路径

        # governed 路径承接 formatter-preserve 输出的 RTL 文本。
        path_case_governed = dict_case_paths["governed"]  # formatter-preserve 准备写入的 governed RTL 目标路径

        # strict JSON 报告路径供自动化读取和回归比较复用。
        path_report_json = dict_case_paths["strict_report_json"]  # 当前 case 的 strict JSON 报告路径

        # strict Markdown 报告路径供人工检查 representative 结果复用。
        path_report_md = dict_case_paths["strict_report_md"]  # 供人工检查 representative 结果的 strict Markdown 报告路径

        # verify-existing 目录只在坏例路径下真正执行并承接工件。
        path_verify_existing_dir = dict_case_paths["verify_existing_dir"]  # 当前 case 的 verify-existing 工件目录

        # 先复制源 RTL，确保后续 governed 处理不改动 tests/cases 原件。
        path_case_source.write_bytes(path_source_reference.read_bytes())

        # copy_source 阶段记录复制后的副本位置，便于后续回放。
        dict_stages["copy_source"] = {
            "status": "completed",  # source 副本复制已经完成
            "path": str(path_case_source),  # source 副本的落盘路径
        }

        # formatter-preserve 需要先读取源 RTL 并返回源编码信息。
        str_source_text, str_source_encoding = read_verilog_source(path_case_source)  # source 副本读取得到的 RTL 文本与编码

        # preserve backend 保证 governed RTL 尽量贴近原始结构与编码语义。
        process_formatter_backend = create_formatter_backend(profile="formatter-preserve")  # 生成 governed RTL 时使用的 preserve formatter 后端

        # governed RTL 文本由 preserve backend 基于 source 副本生成。
        str_governed_text = process_formatter_backend.format_text(  # preserve backend 产出的 governed RTL 文本
            str_source_text,  # formatter-preserve 的源 RTL 文本输入
            path_case_source,  # formatter-preserve 诊断时引用的 source 副本路径
        )

        # 把 governed RTL 文本稳定写到单 case 合同要求的位置。
        path_case_governed.write_text(str_governed_text, encoding="utf-8")

        # formatter-preserve 阶段记录生成结果与命中的源编码。
        dict_stages["formatter_preserve"] = {
            "status": "completed",  # preserve 格式化阶段已经完成
            "profile": "formatter-preserve",  # 当前使用的 formatter profile
            "source_encoding": str_source_encoding,  # 读取源 RTL 时命中的编码名称
            "path": str(path_case_governed),  # governed RTL 的落盘路径
        }

        # strict 质量门配置固定为 normalize profile，并同时写 JSON/Markdown 报告。
        dict_strict_config = {
            "strict": True,  # 当前 representative case 以严格模式运行质量门
            "formatter_profile": "formatter-normalize",  # strict 质量门使用的 formatter profile
            "report_json": path_report_json,  # strict 阶段要写给自动化读取的 JSON 报告路径
            "report_md": path_report_md,  # strict 阶段要写给人工查看的 Markdown 报告路径
        }  # 当前 case 的 strict 质量门配置字典

        # governed RTL 在这里进入 strict 质量门检查。
        dict_strict_report = check_verilog_quality(  # strict 质量门返回的完整报告字典
            path_case_governed,  # 当前 governed RTL 的待检路径
            config=dict_strict_config,  # 当前样例的 strict 质量门固定配置
        )

        # strict 质量门的通过状态直接取自报告字典中的 ok 字段。
        bool_strict_ok = bool(dict_strict_report.get("ok"))  # strict 质量门从报告里读出的通过标记

        # strict 质量门阶段摘要在这里统一整理，减少主流程里的字面量噪声。
        dict_stages["strict_quality_gate"] = _strict_quality_stage_payload(  # 当前样例的 strict 质量门阶段摘要
            bool_strict_ok,  # strict 质量门是否通过
            dict_strict_report,  # strict 质量门返回的完整报告
            path_report_json,  # strict JSON 报告的落盘路径
            path_report_md,  # 供人工浏览 strict 结论的 Markdown 文件路径
        )

        # 只有 bad cohort 才需要继续进入 verify-existing 验证路径。
        if dict_case_spec["cohort"] == "bad":

            # verify-existing 复用 conservative + static 的固定 representative 合同。
            dict_verify_config = {
                "out_dir": path_verify_existing_dir,  # 坏例 verify-existing 工件要落到的目录
                "automation_mode": "conservative",  # 坏例路径只允许保守自动化策略
                "readiness": "static",  # 坏例 verify-existing 固定运行在 static 档位
                "run_external": False,  # 坏例 representative 路径默认不触发外部验证链
            }  # 当前坏例的 verify-existing 配置字典

            # 坏例 governed RTL 在这里进入 verify-existing 流程。
            dict_verify_result = verify_existing_verilog(  # verify-existing 返回的完整结果字典
                path_case_governed,  # 当前坏例对应的 governed RTL 路径
                config=dict_verify_config,  # 当前坏例的 verify-existing 固定配置
            )

            # verify-existing 状态文本优先沿用 runtime 返回字段。
            str_verify_status = str(dict_verify_result.get("status") or "completed")  # 当前坏例的 verify-existing 状态文本

            # verify-existing 阶段记录结果状态与运行目录。
            dict_stages["verify_existing"] = {
                "status": "completed",  # verify-existing 阶段已经执行完成
                "result_status": str_verify_status,  # verify-existing 产出的最终状态文本
                "run_dir": str(path_verify_existing_dir),  # verify-existing 工件目录路径
            }

        # ideal cohort 不需要进入 verify-existing，仅记录跳过原因。
        else:

            # 跳过记录明确说明 verify-existing 只服务 representative 坏例。
            dict_stages["verify_existing"] = {
                "status": "skipped",  # 当前 ideal case 不执行 verify-existing
                "reason": "verify-existing only runs for representative bad cases.",  # verify-existing 跳过原因
            }

        # 所有阶段都正常完成后，把整体状态标记为 completed。
        dict_stage_status["status"] = "completed"  # 当前单 case 主流程已经完整走完

    # 任何异常都需要转成 failed 状态并记录错误文本。
    except Exception as exc:

        # 当前 case 进入 failed 状态，便于 summary 聚合失败数量。
        dict_stage_status["status"] = "failed"  # 当前单 case 主流程因为异常转为 failed

        # 错误文本直接写入阶段状态，供后续 handoff 与排障复用。
        dict_stage_status["error"] = str(exc)  # 当前异常文本会写入 stage_status 供排障复用

    # 无论成功还是失败，都必须把 stage_status 稳定落盘。
    finally:

        # 单 case 阶段状态写出后，后续 summary 才能稳定引用该文件。
        _write_json(dict_case_paths["stage_status"], dict_stage_status)

    # 返回当前 representative case 的稳定摘要字典。
    return _case_summary_payload(
        dict_case_spec,
        dict_case_paths,
        dict_stage_status,
        bool_strict_ok,
        str_verify_status,
    )

# _representative_summary_payload 负责构造全局 run-cases 摘要字典。
def _representative_summary_payload(
    path_run_dir: Path,
    list_case_results: list[dict[str, Any]],
    int_completed_cases: int,
) -> dict[str, Any]:
    """构造 representative run-cases 的全局摘要字典。

    参数:
        path_run_dir: 当前 representative run 的根目录。
        list_case_results: 全部单 case 摘要列表。
        int_completed_cases: 当前 run 中状态为 completed 的 case 数量。

    返回:
        返回供 summary.json 和 summary.md 复用的全局摘要字典。
    """

    # 全部完成时把状态标记为 completed，否则保持 failed。
    str_status = "completed" if int_completed_cases == len(list_case_results) else "failed"  # representative run 的整体状态文本

    # 返回 representative run 的稳定摘要结构。
    return {
        "status": str_status,  # representative run 的整体状态
        "run_dir": str(path_run_dir),  # 当前 representative run 的根目录
        "case_count": len(list_case_results),  # 当前 run 包含的总 case 数量
        "completed_cases": int_completed_cases,  # 当前 run 中成功完成的 case 数量
        "failed_cases": len(list_case_results) - int_completed_cases,  # 当前 run 中失败的 case 数量
        "cases": list_case_results,  # 全部单 case 的稳定摘要列表
    }

# _representative_summary_lead_lines 生成 summary.md 固定不变的头部行。
def _representative_summary_lead_lines(dict_summary: dict[str, Any]) -> list[str]:
    """生成 summary.md 固定不变的标题、计数和表头行。

    参数:
        dict_summary: representative run 的全局摘要字典。

    返回:
        返回 summary.md 在追加 case 行之前的固定头部行列表。
    """

    # 返回 summary.md 开头固定不变的标题、计数摘要和表头行。
    return [
        "# representative-10",  # Markdown 标题行
        "",  # 标题与摘要字段之间的空行
        f"- status: {dict_summary['status']}",  # 代表当前 run 的整体状态
        f"- case_count: {dict_summary['case_count']}",  # 代表当前 run 的样例总数
        f"- completed_cases: {dict_summary['completed_cases']}",  # 代表当前 run 的完成样例数
        f"- failed_cases: {dict_summary['failed_cases']}",  # 代表当前 run 的失败样例数
        "",  # 摘要字段与表头之间的空行
        "| case_id | cohort | status | strict_ok | verify_existing |",  # representative 结果表头
        "| --- | --- | --- | --- | --- |",  # representative 结果表格分隔线
    ]

# _representative_summary_markdown 把全局摘要转换为 Markdown 文本。
def _representative_summary_markdown(dict_summary: dict[str, Any]) -> str:
    """把 representative 全局摘要转换为 Markdown 文本。

    参数:
        dict_summary: representative run 的全局摘要字典。

    返回:
        返回可直接写入 summary.md 的 Markdown 文本。
    """

    # 先拿到 summary.md 固定不变的标题和表头行，后续只追加每个 case 的表格行。
    list_lines = _representative_summary_lead_lines(dict_summary)  # summary.md 头部固定行的逐行文本列表

    # 逐个样例追加表格行，保持 summary.md 与 summary.json 一致。
    for dict_case in dict_summary.get("cases", []):

        # verify 状态缺失时统一显示 n/a，便于理想样例阅读。
        str_verify_display = str(dict_case.get("verify_existing_status") or "n/a")  # 当前 case 的 verify-existing 展示文本

        # strict_ok 在 Markdown 中统一转成 yes 或 no。
        str_strict_display = "yes" if dict_case.get("strict_ok") else "no"  # 当前 case 的 strict 质量门展示文本

        # 把当前 case 的摘要追加到 Markdown 表格。
        list_lines.append(
            f"| {dict_case['case_id']} | {dict_case['cohort']} | {dict_case['status']} | "
            f"{str_strict_display} | {str_verify_display} |",
        )

    # 返回带换行结尾的 Markdown 文本，便于文件对比和追加查看。
    return "\n".join(list_lines) + "\n"

# run_verilog_cases 提供 representative corpus 的公共 facade 入口。
def run_verilog_cases(
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """运行 representative existing-RTL corpus，并写出固定摘要与报告。

    参数:
        config: 新式 facade 配置字典；缺省时只使用 legacy_options。
        legacy_options: 旧调用方传入的兼容关键字参数。

    返回:
        返回 representative run 的全局稳定摘要字典。
    """

    # run-cases 白名单文本先单独命名，避免集合定义行过长。
    str_allowed_keys_text = "out_dir case"  # run-cases 入口允许的兼容键文本

    # 再把白名单文本切分成集合，供兼容配置合并逻辑复用。
    set_allowed_keys = set(str_allowed_keys_text.split())  # run-cases 入口允许的兼容配置键集合

    # 合并 config 与 legacy_options，得到统一的 run-cases 配置视图。
    dict_options = _merged_option_dict(  # run-cases 入口合并后的统一配置字典
        "run_verilog_cases",  # 当前 facade 入口名称
        config,  # 新式调用方传入的配置字典
        legacy_options,  # 旧式关键字参数载荷
        allowed_keys=set_allowed_keys,  # run-cases 允许透传的字段集合
    )

    # run 根目录缺省落在 representative-10，保持旧合同不变。
    path_run_dir = Path(dict_options.get("out_dir") or DEFAULT_CASE_RUN_DIR)  # representative run 的根目录

    # run 根目录需要提前创建，便于后续按 case 落盘工件。
    path_run_dir.mkdir(parents=True, exist_ok=True)

    # 当前请求的 case 选择在这里统一解析成元信息列表。
    list_selected_cases = _selected_representative_cases(dict_options.get("case"))  # 本次要执行的 representative 元信息列表

    # case 级摘要列表承接每个 representative 样例的稳定结果。
    list_case_results: list[dict[str, Any]] = []  # 本次 representative run 的单 case 摘要列表

    # completed 计数用于汇总全局状态和失败数量。
    int_completed_cases = 0  # 当前 representative run 中完成的 case 数量

    # cases 子目录承接每个 representative 样例的独立运行目录。
    path_cases_root = path_run_dir / "cases"  # 本次 representative run 的 case 根目录

    # 逐个执行选中的 representative 样例，并保持请求顺序稳定。
    for dict_case_spec in list_selected_cases:

        # 当前 case 目录以 case_id 命名，便于人工定位对应工件。
        path_case_dir = path_cases_root / str(dict_case_spec["case_id"])  # 当前 representative case 的独立运行目录

        # 运行单个 representative case，并返回其稳定摘要。
        dict_case_result = _run_single_representative_case(dict_case_spec, path_case_dir)  # 当前 representative case 的稳定摘要结果

        # 把当前 case 的稳定摘要追加到全局结果列表。
        list_case_results.append(dict_case_result)

        # 只有 completed 状态才计入完成数量。
        if dict_case_result["status"] == "completed":

            # completed 计数用于最终 summary 的状态与计数统计。
            int_completed_cases += 1  # 本次 representative run 的完成计数加一

    # 全局摘要在这里统一汇总，供 JSON 和 Markdown 双出口复用。
    dict_summary = _representative_summary_payload(  # 汇总整个 representative run 的最终摘要
        path_run_dir,  # 把摘要绑定到当前 run 的根目录
        list_case_results,  # 汇总所有单样例执行后的稳定结果列表
        int_completed_cases,  # 告诉摘要里有多少样例真正走完主流程
    )

    # summary.json 作为自动化消费入口，始终稳定写出。
    _write_json(path_run_dir / "summary.json", dict_summary)

    # summary.md 作为人工检查入口，始终稳定写出。
    (path_run_dir / "summary.md").write_text(
        _representative_summary_markdown(dict_summary),
        encoding="utf-8",
    )

    # 返回 representative run 的全局稳定摘要字典。
    return dict_summary
