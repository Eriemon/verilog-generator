"""skill_effectiveness 的 case 支持函数与报告组装工具。"""

# 未来注解保持 helper 模块导入轻量，便于主评估入口复用。
from __future__ import annotations

# 标准库负责 JSON fixture、工作目录切换和上下文管理。
import contextlib
import json
import os
import shutil
import time
from pathlib import Path
from types import TracebackType
from typing import Any

# verify-existing facade API 用于 patch-library 评估分支。
from scripts.python.facade.verilog_api import verify_existing_verilog

# skill 根解析沿用 runtime config，保持 evals.json 相对路径语义。
from scripts.python.workflow.config import skill_root
from scripts.python.workflow.workspace import workspace_root

# 固定 skill 根用于解析 evals.json 中的 fixture 路径。
SKILL_ROOT = skill_root()  # skill 主体根目录

# 生成本轮 eval 运行目录，目录名携带进程号降低并发冲突概率。
def _temporary_eval_root() -> Path:
    """生成当前进程专属的 skill-effectiveness 临时目录。

    :param 无: 不接收外部业务参数，目录名由进程号和时间戳生成。
    :return: 已创建的本轮 eval 临时根目录。
    """

    # 时间戳只参与目录命名，不参与评估语义。
    int_timestamp = int(time.time())  # 当前秒级时间戳

    # smoke 根沿用 workspace_root，使源码仓库和安装副本都能正确落位。
    path_workspace_root = workspace_root()  # eval 临时目录所在的工作区根

    # 运行目录名携带进程与秒级时间，降低并行 eval 之间的目录冲突。
    str_run_name = f"skill-effectiveness-{os.getpid()}-{int_timestamp}"  # 本轮 eval 目录名

    # 临时目录统一放在 _smoke_runs 下，便于清理脚本识别。
    path_temp_root = path_workspace_root / "_smoke_runs" / str_run_name  # 当前评估运行目录

    # mkdir 保证后续 case 可以直接写入子目录。
    path_temp_root.mkdir(parents=True, exist_ok=True)

    # 调用方负责 finally 清理该目录。
    return path_temp_root

# 清理当前临时目录并在 smoke 根为空时移除空目录。
def _cleanup_temp_root(path_temp_root: Path) -> None:
    """删除本次 eval 的临时目录，保留其他并发运行目录。

        :param path_temp_root: 本轮 eval 创建的临时根目录。
        :return: 无返回值，目录清理由文件系统副作用完成。
        """

    # 本轮目录存在时才执行递归清理，避免误删不存在路径的异常噪声。
    if path_temp_root.exists():

        # ignore_errors 避免 Windows 文件句柄短暂占用导致主评估失败。
        shutil.rmtree(path_temp_root, ignore_errors=True)

    # 父目录可能被其他 worker 共享，只有为空时才移除。
    path_smoke_root = path_temp_root.parent  # 评估临时目录所在的 smoke 根

    # 空目录检查必须放在 suppress 内，兼容并发创建/删除的竞态。
    if path_smoke_root.exists():

        # 并发 worker 可能同时处理 smoke 根，忽略瞬时文件系统异常。
        with contextlib.suppress(OSError):

            # 没有剩余子项时删除根目录，保持 smoke 工作区整洁。
            if not any(path_smoke_root.iterdir()):

                # 仅删除空目录，不影响兄弟运行工件。
                path_smoke_root.rmdir()

# 汇总本地 case 的通过条件。
def _local_cases_passed(list_case_reports: list[dict[str, Any]]) -> bool:
    """判断所有本地 eval case 是否同时通过、改进且稳定。

        :param list_case_reports: 本地 eval case 的结构化报告列表。
        :return: 所有 case 同时 passed、improved、stable 时返回 True。
        """

    # 空报告必须显式失败，避免 Python 空集合全真语义掩盖缺失用例。
    if not list_case_reports:

        # 没有 case 时返回失败，和入口校验保持一致。
        return False

    # 每个 case 都必须声明自身通过。
    bool_passed = all(bool(dict_case.get("passed")) for dict_case in list_case_reports)  # case 顶层 passed 全部为真

    # 启用 skill 后必须优于 baseline，否则 skill-effectiveness 没有意义。
    bool_improved = all(bool(dict_case.get("comparison", {}).get("improved")) for dict_case in list_case_reports)  # comparison.improved 全部为真

    # stable 表示已生成或分析的工件满足该 case 的可复验要求。
    bool_stable = all(bool(dict_case.get("with_skill", {}).get("stable")) for dict_case in list_case_reports)  # with-skill 产物稳定标志全部为真

    # 三个维度同时满足才允许本地 eval 通过。
    return bool_passed and bool_improved and bool_stable

# 组装 summary，字段名保持旧验证脚本兼容。
def _overall_summary(
    list_case_reports: list[dict[str, Any]],
    dict_remote_report: dict[str, Any],
    *,
    require_remote: bool,
) -> dict[str, Any]:
    """把本地 case 与远程 run 证据整理成 summary 字段。

        :param list_case_reports: 本地 eval case 的结构化报告列表。
        :param dict_remote_report: 远程 retained run 检查摘要。
        :param require_remote: 当前评估是否要求远程证据通过。
        :return: 兼容旧报告 schema 的 summary 字典。
        """

    # passed_cases 统计每个 case 的最终 passed 字段。
    int_passed_cases = sum(1 for dict_case in list_case_reports if dict_case.get("passed"))  # 顶层通过的 case 数量

    # improved_cases 统计启用 skill 后相对 baseline 的收益数量。
    int_improved_cases = sum(1 for dict_case in list_case_reports if dict_case.get("comparison", {}).get("improved"))  # 具备 skill 增益的 case 数量

    # stable_cases 统计生成或分析链路稳定的 case 数量。
    int_stable_cases = sum(1 for dict_case in list_case_reports if dict_case.get("with_skill", {}).get("stable"))  # 产物稳定的 case 数量

    # 返回结构保持旧报告字段，ok 由调用方补入。
    return {
        "case_count": len(list_case_reports),
        "passed_cases": int_passed_cases,
        "improved_cases": int_improved_cases,
        "stable_cases": int_stable_cases,
        "remote_verified": dict_remote_report["checked"] and dict_remote_report["ok"],
        "remote_required": require_remote,
    }

# 从 case 构造可选 source 路径，支持缺失文件模拟。
def _routing_source_path(case: dict[str, Any], path_case_root: Path) -> Path | None:
    """解析 routing case 的 RTL 输入路径或缺失占位路径。

        :param case: evals.json 中的单个 routing case 配置。
        :param path_case_root: 单个 case 的临时工件目录。
        :return: 可传给 route 的 RTL 路径；没有 source 时返回 None。
        """

    # 默认没有 source 时 route 输入为 None。
    path_source = _optional_skill_path(case, "source")  # route 的源 RTL 路径

    # missing_source 用不存在的 case-local 路径模拟用户传错文件。
    if case.get("missing_source"):

        # 该路径不写入文件，route 应报告缺失。
        path_source = path_case_root / "missing_rtl.v"  # 故意不存在的 RTL 输入路径

    # 返回 None 或 Path，交给 route_verilog_request 判断。
    return path_source

# routing artifact_dir 支持按需生成 spec/codegen_plan 文件。
def _routing_artifact_dir(case: dict[str, Any], case_root: Path) -> Path | None:
    """根据 routing case 配置创建 artifact_dir。

        :param case: evals.json 中的单个 routing case 配置。
        :param case_root: 单个 case 的临时工件目录。
        :return: 已准备好的 artifact_dir；case 未配置时返回 None。
        """

    # artifact_dir 配置不存在时 route 不接收 artifact_dir。
    dict_artifact_config = _optional_dict(case, "artifact_dir")  # route 需要扫描的临时产物配置

    # 非字典配置表示该 case 不需要 artifact_dir。
    if dict_artifact_config is None:

        # 返回 None 保持旧 route 输入行为。
        return None

    # artifact_dir 固定在 case 根下，避免污染 skill 主体。
    path_artifact_dir = case_root / "artifacts"  # route 扫描的临时产物目录

    # 创建 artifact_dir 以便 route 扫描内部文件。
    path_artifact_dir.mkdir(parents=True, exist_ok=True)

    # spec 标记存在时写入最小规格文件。
    if dict_artifact_config.get("spec"):

        # spec.json 只需包含 route 可识别的目标信息。
        (path_artifact_dir / "spec.json").write_text(
            json.dumps({"target": "rtl", "module_name": "routing_eval"}, indent=2),
            encoding="utf-8",
        )

    # codegen_plan 配置存在时写入对应计划文件。
    if isinstance(dict_artifact_config.get("codegen_plan"), dict):

        # 保留 evals.json 提供的 codegen_plan 内容。
        (path_artifact_dir / "codegen_plan.json").write_text(
            json.dumps(dict_artifact_config["codegen_plan"], indent=2),
            encoding="utf-8",
        )

    # 返回可供 route 扫描的 artifact_dir。
    return path_artifact_dir

# routing logs 支持实际日志和缺失日志两种输入。
def _routing_log_paths(case: dict[str, Any], case_root: Path) -> list[Path]:
    """根据 routing case 配置生成日志路径列表。

        :param case: evals.json 中的单个 routing case 配置。
        :param case_root: 单个 case 的临时工件目录。
        :return: route 输入使用的实际日志路径和故意缺失路径列表。
        """

    # logs 允许 evals.json 提供多个命名日志片段。
    list_logs = case.get("logs", [])  # 原始日志配置列表

    # route 输入只收集本 case 写出的日志文件路径。
    list_paths: list[Path] = []  # route 输入日志路径集合

    # 只有列表形式才逐项写出日志。
    if isinstance(list_logs, list):

        # 每个日志条目写入 case 根，便于 route 读取。
        for int_index, dict_item in enumerate(list_logs, start=1):

            # 非字典条目无法提供 name/text，直接跳过。
            if not isinstance(dict_item, dict):

                # 跳过坏条目，保留其他日志继续测试。
                continue

            # 未命名日志使用稳定序号命名。
            str_log_name = str(dict_item.get("name") or f"log_{int_index}.log")  # route 日志文件名

            # 日志固定写在 case 根目录，避免 route 读取跨 case 产物。
            path_log = case_root / str_log_name  # route 日志文件路径

            # 日志文本按原样写入，route 负责解析。
            path_log.write_text(str(dict_item.get("text") or ""), encoding="utf-8")

            # 将该日志纳入 route 输入。
            list_paths.append(path_log)

    # missing_log 用不存在路径模拟日志文件缺失。
    if case.get("missing_log"):

        # 不写入文件，让 route 处理缺失日志。
        list_paths.append(case_root / "missing.log")

    # 返回实际存在和故意缺失的日志路径集合。
    return list_paths

# remote run 报告转换为本 eval 报告的统一 remote 字段。
def _evaluate_remote_runs(
    remote_runs_report: dict[str, Any] | None,
    *,
    require_remote: bool,
) -> dict[str, Any]:
    """检查 retained remote validation run 是否满足 xsim 与 fixture 证据。

        :param remote_runs_report: remote_validate --report-runs 生成的 retained run 报告。
        :param require_remote: 当前评估是否要求远程证据通过。
        :return: remote 字段使用的 checked、ok、backend 和 fixture 摘要。
        """

    # 未提供远程报告时只记录 unchecked，不影响非远程必需场景。
    if not remote_runs_report:

        # reason 字段帮助 release gate 区分未提供与失败。
        return {
            "ok": False,
            "checked": False,
            "required": require_remote,
            "reason": "no remote run report provided",
        }

    # runs 字段来自 remote_validate --report-runs，坏格式时按空列表处理。
    list_runs_candidate = remote_runs_report.get("runs", []) if isinstance(remote_runs_report, dict) else []  # retained run 原始候选集合

    # 非列表 runs 无法作为 retained run 证据。
    list_runs = list_runs_candidate if isinstance(list_runs_candidate, list) else []  # 可验证的 retained run 列表

    # 空 runs 表示报告存在但没有可验证运行。
    if not list_runs:

        # checked=true 表示已看过远程报告但证据不足。
        return {
            "ok": False,
            "checked": True,
            "required": require_remote,
            "reason": "remote run report did not contain any retained runs",
        }

    # 最新运行按 report-runs 的排序约定取第一个。
    dict_latest = list_runs[0]  # report-runs 排序后的最新远程运行

    # remote_execute 子字段携带模拟器可用性与后端选择。
    dict_remote_execute = _optional_nested_dict(dict_latest, "remote_execute")  # 最新运行的远程执行摘要

    # fixtures 字段可能缺失或被损坏为非列表。
    list_fixtures_candidate = dict_latest.get("fixtures", [])  # 最新运行保留的 fixture 候选集合

    # 只有列表 fixture 才能参与通过性统计。
    list_fixtures = list_fixtures_candidate if isinstance(list_fixtures_candidate, list) else []  # 远程 fixture 结果列表

    # 过滤非字典条目，避免坏报告条目参与 get(...) 检查。
    list_fixture_dicts = [dict_item for dict_item in list_fixtures if isinstance(dict_item, dict)]  # 可检查的 fixture 结果字典

    # 所有 fixture 必须 ok，且至少存在一个 fixture。
    bool_fixtures_ok = bool(list_fixture_dicts)  # fixture 检查先要求至少有一条有效记录

    # 任一 fixture 未通过时，远程证据不能算完整。
    for dict_fixture in list_fixture_dicts:

        # ok 字段为假说明该 fixture 未能通过远程保留运行。
        if not dict_fixture.get("ok"):

            # 标记失败后退出循环，保留和 all(...) 等价的短路语义。
            bool_fixtures_ok = False  # fixture 通过性汇总状态

            # 已发现失败 fixture，无需继续扫描后续条目。
            break

    # xsim 是本 skill 当前远程强验证的 canonical 后端。
    bool_remote_available = bool(dict_remote_execute.get("available"))  # 远程执行环境可用状态

    # 远程执行自身需要报告 ok，fixture 通过不能替代执行状态。
    bool_remote_execute_ok = bool(dict_remote_execute.get("ok"))  # 远程执行摘要通过状态

    # 当前 release gate 只接受 xsim 作为强验证后端。
    bool_backend_xsim = dict_remote_execute.get("selected_simulator_backend") == "xsim"  # 远程后端是否为 xsim

    # 四个远程证据维度同时满足才允许 remote.ok 为真。
    bool_remote_ok = bool_remote_available and bool_remote_execute_ok and bool_backend_xsim and bool_fixtures_ok  # retained run 总体验证状态

    # remote 摘要逐字段组装，避免把报告 schema 伪装成实验参数表。
    dict_remote_summary: dict[str, Any] = {}  # remote 字段最终报告容器

    # ok 字段表达 retained run 是否满足全部强验证证据。
    dict_remote_summary["ok"] = bool_remote_ok  # retained run 总体通过状态

    # checked 表示本函数已经检查过远程报告内容。
    dict_remote_summary["checked"] = True  # 远程报告已检查标志

    # required 保留调用方对远程验证的强制要求。
    dict_remote_summary["required"] = require_remote  # 当前评估是否要求远程证据

    # latest_run 记录 report-runs 排序后的首个运行标识。
    dict_remote_summary["latest_run"] = dict_latest.get("run")  # 最新 retained run 标识

    # selected_simulator_backend 用于证明强验证后端确实为 xsim。
    dict_remote_summary["selected_simulator_backend"] = dict_remote_execute.get("selected_simulator_backend")  # retained run 选择的仿真后端

    # fixture_count 让报告使用者确认本次远程证据覆盖了 fixture。
    dict_remote_summary["fixture_count"] = len(list_fixtures)  # retained run 中的 fixture 数量

    # 返回 remote 字段的稳定摘要。
    return dict_remote_summary

# expectation checks 用于默认 prompt regression case。
def _expectation_checks(
    *,
    prompt: str,
    requirements: dict[str, Any],
    codegen_plan: dict[str, Any],
    pattern_templates: list[str],
    expectations: dict[str, Any],
) -> dict[str, bool]:
    """根据 eval expectations 检查 prompt、requirements 和 codegen plan。

        :param prompt: with-skill 或 baseline 生成的提示词文本。
        :param requirements: requirements 阶段输出的结构化需求。
        :param codegen_plan: codegen_plan 阶段输出的生成计划。
        :param pattern_templates: 进入 prompt 的 pattern template id 序列。
        :param expectations: evals.json 中声明的期望检查项。
        :return: 每个启用 expectation 的布尔检查结果。
        """

    # checks 只包含 evals.json 明确要求的字段。
    dict_checks: dict[str, bool] = {}  # prompt regression 检查集合

    # rtl_style_profile 检查 Erie strict 提示是否进入 prompt。
    if "rtl_style_profile" in expectations:

        # strict profile 需要核心命名提示同时出现。
        bool_has_strict_profile = expectations["rtl_style_profile"] == "erie_strict"  # 期望 profile 是否为 Erie strict

        # state_current/state_next 是 Erie strict 命名约束进入 prompt 的可观察信号。
        bool_prompt_names_states = "state_current" in prompt and "state_next" in prompt  # prompt 是否包含状态命名约束

        # strict profile 名称和状态命名约束必须同时出现。
        bool_rtl_style_ok = bool_has_strict_profile and "erie_strict" in prompt and bool_prompt_names_states  # RTL style profile 检查结果

        # 结果按 expectations key 写回，保持报告字段稳定。
        dict_checks["rtl_style_profile"] = bool_rtl_style_ok  # Erie strict 命名提示命中状态

    # use-case template 选择必须记录在 requirements。
    if "selected_use_case_template_id" in expectations:

        # 期望模板 id 单独命名，避免一行比较过长。
        str_expected_template_id = expectations["selected_use_case_template_id"]  # evals.json 期望的 use-case 模板 id

        # 精确匹配 evals.json 声明的模板 id。
        bool_use_case_template_ok = requirements.get("selected_use_case_template_id") == str_expected_template_id  # use-case template id 是否匹配

        # 记录模板选择检查，供 with/baseline pass count 使用。
        dict_checks["selected_use_case_template_id"] = bool_use_case_template_ok  # 模板选择检查结果

    # prompt 是否包含 use-case template section。
    if "requires_use_case_section" in expectations:

        # 期望值控制该 section 是否必须出现。
        bool_use_case_section_expected = bool(expectations["requires_use_case_section"])  # use-case section 期望存在性

        # bool(...) 保持旧逻辑，只比较 section 存在性。
        bool_use_case_section_ok = ("## Use-case template" in prompt) is bool_use_case_section_expected  # use-case section 存在性检查结果

        # use-case 段落结果写入报告，证明模板正文是否注入 prompt。
        dict_checks["requires_use_case_section"] = bool_use_case_section_ok  # prompt 是否按需包含 use-case 模板段

    # pattern template id 列表必须保持顺序一致。
    if "selected_pattern_template_ids" in expectations:

        # 复制期望列表，避免外部对象被后续逻辑修改。
        list_expected_ids = list(expectations["selected_pattern_template_ids"])  # 期望 pattern template id 序列

        # pattern template 顺序影响 prompt 生成内容，必须精确一致。
        bool_improved_ids_ok = list(pattern_templates) == list_expected_ids  # pattern template id 顺序检查结果

        # improved id 顺序结果写入报告，覆盖模板选择的顺序语义。
        dict_checks["selected_pattern_template_ids"] = bool_improved_ids_ok  # pattern template id 顺序是否正确

    # 检查 prompt 中是否按要求带有 improved 模板段。
    if "requires_pattern_template_section" in expectations:

        # evals.json 决定 improved 模板段是否必须出现在 prompt。
        bool_improved_section_expected = bool(expectations["requires_pattern_template_section"])  # 是否要求 improved 模板段

        # prompt 中的 improved 模板段存在性必须和期望一致。
        bool_improved_section_ok = ("## Verilog pattern templates" in prompt) is bool_improved_section_expected  # improved 模板段存在性是否符合期望

        # 该字段证明 improved 设计模式是否真正进入 prompt。
        dict_checks["requires_pattern_template_section"] = bool_improved_section_ok  # improved 设计模式段落注入状态

    # ready_for_generation 检查 codegen plan 的最终准备度。
    if expectations.get("ready_for_generation") is not None:

        # 使用 is 保持布尔值精确判断。
        bool_ready_for_generation_ok = codegen_plan.get("ready_for_generation") is expectations["ready_for_generation"]  # codegen plan 准备度检查结果

        # codegen plan 准备度结果进入 expectation_checks。
        dict_checks["ready_for_generation"] = bool_ready_for_generation_ok  # 生成准备度检查结果

    # 返回仅包含启用项的检查字典。
    return dict_checks

# 从 with/baseline 检查构造对比字段。
def _comparison_from_baseline(
    dict_with_checks: dict[str, bool],
    dict_baseline_checks: dict[str, bool],
) -> dict[str, Any]:
    """比较 with-skill 与 without-skill 的通过项数量。

        :param dict_with_checks: skill 启用后得到的检查结果。
        :param dict_baseline_checks: baseline prompt 得到的检查结果。
        :return: with/baseline 通过项数量和 improved 结论。
        """

    # skill 注入后的检查集合用于计算增强侧命中数量。
    int_with_count = _pass_count(dict_with_checks)  # with-skill 通过项数量

    # baseline 计数来自未注入 skill 上下文的泛化 prompt。
    int_without_count = _pass_count(dict_baseline_checks)  # 未注入上下文时命中的 expectation 数量

    # improved 表示 skill 上下文相对 baseline 增加了期望命中项。
    return {
        "with_skill_pass_count": int_with_count,
        "without_skill_pass_count": int_without_count,
        "improved": int_with_count > int_without_count,
    }

# 标准 checks 报告用于大多数非默认 case。
def _report_from_checks(
    case: dict[str, Any],
    str_case_id: str,
    dict_checks: dict[str, bool],
    # 可选字段只在部分 case 的历史报告 schema 中出现。
    *,
    source: str | None = None,
    spec: str | None = None,
    stable: bool | None = None,
    with_skill_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """根据 checks 生成 skill-effectiveness 的标准 case 报告。

        :param case: evals.json 中的单个 case 配置。
        :param str_case_id: 当前 case 的稳定标识。
        :param dict_checks: with-skill 侧已经执行的检查结果。
        :param source: 可选输入源文件路径文本。
        :param spec: 可选规格文件路径文本。
        :param stable: 可选稳定性覆盖值；缺失时沿用 passed。
        :param with_skill_extra: 需要合并到 with_skill 字段的 case 专属证据。
        :return: 兼容旧报告结构的单个 case 报告。
        """

    # 每个检查项均通过时，该 case 才通过。
    bool_passed = all(dict_checks.values())  # 当前 case 的 passed 结果

    # 未显式指定 stable 时，stable 与 passed 保持一致。
    bool_stable = bool_passed if stable is None else stable  # with_skill 稳定性布尔值

    # without_skill 默认所有检查为 false，用于证明 skill 增益。
    dict_without_checks: dict[str, bool] = {}  # baseline 侧每个启用检查的固定失败映射

    # baseline 对照需要覆盖 with-skill 实际启用的每个检查项。
    for str_key in dict_checks:

        # baseline 不具备对应 skill 能力时的默认值为失败。
        dict_without_checks[str_key] = False  # baseline 对照项默认未命中

    # comparison 复用 pass count 逻辑，保持 improved 字段兼容。
    dict_comparison = _comparison_from_baseline(dict_checks, dict_without_checks)  # with/baseline 通过项对比

    # with_skill 至少包含 stable 和 expectation_checks。
    dict_with_skill = {
        "stable": bool_stable,  # 标准报告中的稳定性状态
        "expectation_checks": dict_checks,  # with-skill 实际检查结果
    }  # with-skill 标准报告主体

    # 附加字段用于 route_decision、blocked_codes 等 case 专属证据。
    if with_skill_extra:

        # update 保留调用方提供的键名，不改变旧报告字段。
        dict_with_skill.update(with_skill_extra)

    # 顶层字段按历史报告顺序组织，便于 diff。
    dict_report: dict[str, Any] = {"id": str_case_id, "kind": case.get("kind")}  # 后续追加 source/spec/passed/comparison 的 case 报告映射

    # source/spec 只有对应 case 旧报告使用时才写入。
    if source is not None:

        # source 字段保留 evals.json 中的相对路径字符串。
        dict_report["source"] = source  # case 报告中的 source 输入引用

    # spec 字段保留生成类 case 的输入规格路径。
    if spec is not None:

        # spec 字段用于测试和人工报告定位输入。
        dict_report["spec"] = spec  # case 报告中的规格文件引用

    # 合并稳定的 case 报告字段。
    dict_report.update(
        {
            "passed": bool_passed,  # 标准 case 是否通过
            "with_skill": dict_with_skill,  # skill 启用后的证据块
            "without_skill": {"expectation_checks": dict_without_checks},  # baseline 对照检查
            "comparison": dict_comparison,  # with/baseline 通过项比较
            "pattern_templates": [],  # 非默认 case 不计算模板摘要
        }
    )

    # 返回完整 case 报告。
    return dict_report

# 根据 expectations 启用或关闭检查项。
def _enabled_checks(
    dict_candidates: dict[str, bool],
    dict_expectations: dict[str, Any],
) -> dict[str, bool]:
    """按 eval expectations 保留启用项，未启用项视为通过。

        :param dict_candidates: 候选检查项的真实执行结果。
        :param dict_expectations: evals.json 中控制检查启用状态的 expectations。
        :return: 已按 expectations 屏蔽未启用项的检查结果。
        """

    # 空 expectations 时保留候选检查的真实结果。
    if not dict_expectations:

        # 没有显式开关的 case 使用候选检查原值。
        return dict_candidates

    # 旧逻辑：expectations 中明确为 false 的检查项不阻断 case。
    return {
        str_key: bool_value if dict_expectations.get(str_key, True) else True
        for str_key, bool_value in dict_candidates.items()
    }

# 从 case 中读取 expectations，坏格式按空期望处理。
def _case_expectations(case: dict[str, Any]) -> dict[str, Any]:
    """返回 case.expectations 字典，非字典输入降级为空字典。

        :param case: evals.json 中的单个 case 配置。
        :return: 可安全读取的 expectations 字典。
        """

    # evals.json 允许部分 case 省略 expectations。
    dict_expectations = case.get("expectations", {})  # eval case 中的 expectations 候选字段

    # 只有字典才参与后续检查。
    if isinstance(dict_expectations, dict):

        # 返回原字典即可，调用方只读使用。
        return dict_expectations

    # 非字典 expectations 视为没有额外要求。
    return {}

# 读取 JSON 文件并返回字典，集中处理类型语义。
def _read_json(path_json: Path) -> dict[str, Any]:
    """读取 UTF-8 JSON 文件并返回顶层字典。

        :param path_json: 需要读取的 JSON 文件路径。
        :return: JSON 顶层对象。
        :raises ValueError: JSON 顶层不是对象时抛出。
        """

    # json.loads 保持与旧实现一致的异常类型。
    dict_payload = json.loads(path_json.read_text(encoding="utf-8"))  # JSON 顶层对象

    # 评估产物约定为字典，非字典直接返回空字典会掩盖错误。
    if not isinstance(dict_payload, dict):

        # 报错包含路径，便于定位损坏产物。
        raise ValueError(f"> ERR: [Python] JSON payload must be an object: {path_json}")

    # 返回解析出的 JSON 对象。
    return dict_payload

# 可选路径字段解析到 skill 根。
def _optional_skill_path(case: dict[str, Any], str_key: str) -> Path | None:
    """把 case 中的可选相对路径解析到 skill 根。

        :param case: evals.json 中的单个 case 配置。
        :param str_key: 需要解析的路径字段名。
        :return: 解析后的 skill-local 路径；字段缺失或为空时返回 None。
        """

    # 缺失或空字段表示调用方不提供该路径。
    if not case.get(str_key):

        # 返回 None，让 facade 使用自己的默认行为。
        return None

    # 所有 evals.json 相对路径都以 SKILL_ROOT 为根。
    return SKILL_ROOT / str(case[str_key])

# 读取可选字典字段。
def _optional_dict(case: dict[str, Any], str_key: str) -> dict[str, Any] | None:
    """仅当 case 字段为字典时返回该字段。

        :param case: evals.json 中的单个 case 配置。
        :param str_key: 需要读取的结构化字段名。
        :return: 字段为字典时返回原字典，否则返回 None。
        """

    # 获取原始字段，保留 None 与非字典的区别。
    obj_value = case.get(str_key)  # 待读取的可选字段

    # 只有 dict 才能作为下游结构化输入。
    if isinstance(obj_value, dict):

        # 返回字典字段供 route 或检查逻辑使用。
        return obj_value

    # 非字典字段不参与结构化输入。
    return None

# 读取嵌套字典字段，缺失时返回空字典。
def _optional_nested_dict(
    dict_payload: dict[str, Any],
    str_key: str,
) -> dict[str, Any]:
    """从字典中安全读取嵌套字典。

        :param dict_payload: 需要读取子字段的父级字典。
        :param str_key: 嵌套字典字段名。
        :return: 字段为字典时返回原字典，否则返回空字典。
        """

    # 远程报告中的子字段可能缺失或为非字典。
    obj_value = dict_payload.get(str_key)  # 嵌套字段候选值

    # 类型正确时直接返回。
    if isinstance(obj_value, dict):

        # 调用方只读使用该嵌套字典。
        return obj_value

    # 缺失或类型不符时返回空字典，便于 get(...) 链式读取。
    return {}

# 创建 case 根目录。
def _prepare_case_root(temp_root: Path, str_case_id: str) -> Path:
    """创建并返回单个 eval case 的工件目录。

        :param temp_root: 本轮 eval 的临时根目录。
        :param str_case_id: 当前 case 的稳定标识。
        :return: 已创建的单 case 工件目录。
        """

    # case_id 直接参与路径名，evals.json 中应保持稳定。
    path_case_root = temp_root / str_case_id  # 单 case 工件目录

    # 创建目录后各 evaluator 可以直接写入工件。
    path_case_root.mkdir(parents=True, exist_ok=True)

    # 返回目录路径供调用方继续拼接子目录。
    return path_case_root

# 复制 fixture 到目标目录，可选重命名。
def _copy_fixture(
    path_source: Path,
    path_target_dir: Path,
    str_name: str | None = None,
) -> Path:
    """复制 fixture 文件到 case-local 目录并返回副本路径。

        :param path_source: skill 内原始 fixture 文件路径。
        :param path_target_dir: case-local 目标目录。
        :param str_name: 可选目标文件名；缺失时沿用源文件名。
        :return: 复制后的 case-local fixture 路径。
        """

    # 目标名默认沿用源文件名，特殊 case 可传入 str_name。
    str_target_name = str_name or path_source.name  # 目标 fixture 文件名

    # 目标路径落在 case-local 目录内，避免改动 skill 原始 fixture。
    path_target = path_target_dir / str_target_name  # 目标 fixture 路径

    # 创建父目录，避免调用方每次手写 mkdir。
    path_target.parent.mkdir(parents=True, exist_ok=True)

    # 使用文本复制，保持原 fixture 编码与旧实现一致。
    path_target.write_text(path_source.read_text(encoding="utf-8"), encoding="utf-8")

    # 返回副本路径供 verify/compare 调用。
    return path_target

# 多文件阻断输入需要复制到 case-local 目录。
def _copy_blocked_sources(
    case: dict[str, Any],
    path_case_root: Path,
) -> list[Path]:
    """复制 blocked_sources 中的 RTL fixture。

        :param case: evals.json 中的单个 case 配置。
        :param path_case_root: 单个 case 的临时工件目录。
        :return: 可传给 verify-existing 的本地 RTL 副本路径列表。
        """

    # blocked_sources 可为空，空列表会让后续 verify 自行处理。
    list_local_sources: list[Path] = []  # 本地多文件 RTL 副本集合

    # 每个源文件复制到 case 根，避免修改 skill example。
    for str_source_ref in case.get("blocked_sources", []):

        # 源文件路径以 skill 根为基准。
        path_source = SKILL_ROOT / str(str_source_ref)  # 阻断场景源 RTL

        # 复制后的路径加入 verify-existing 输入列表。
        list_local_sources.append(_copy_fixture(path_source, path_case_root))

    # 返回本地副本列表。
    return list_local_sources

# 写入确认应用 RTL patch 的 decision.json。
def _write_decision(path_decision: Path, *, evidence: str) -> Path:
    """写入 verify-existing resume 使用的确认决策文件。

        :param path_decision: decision.json 的目标写入路径。
        :param evidence: 模拟用户确认 patch 的证据文本。
        :return: 写入完成的 decision.json 路径。
        """

    # decision payload 模拟用户已确认应用 patch。
    dict_decision = {
        "version": 1,  # decision 文件格式版本
        "status": "resolved",  # 表示人工确认已经闭环
        "decision": "apply_rtl_patch",  # resume 时采用的动作
        "evidence": [evidence],  # 模拟用户确认 patch 的证据
        "constraints": ["preserve interface"],  # 应用 patch 时必须保持接口
        "affected_subfunctions": ["*"],  # 当前 fixture 允许影响全部子功能
    }  # verify-existing 决策文件内容

    # 确保 decision 目录存在，支持嵌套 run 目录。
    path_decision.parent.mkdir(parents=True, exist_ok=True)

    # 写入格式保持旧实现的 indent 与 UTF-8。
    path_decision.write_text(
        json.dumps(dict_decision, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 返回路径供 verify_existing_verilog resume 调用。
    return path_decision

# patch library 单分支执行首跑和确认 resume。
def _run_patch_library_branch(
    *,
    source: Path,
    spec_source: Path,
    case_root: Path,
    branch_name: str,
    evidence: str,
) -> dict[str, Any]:
    """执行 control/timing patch-library 分支并收集关键证据。

        :param source: 原始 RTL fixture 路径。
        :param spec_source: patch-library case 使用的规格路径。
        :param case_root: 单个 case 的临时工件目录。
        :param branch_name: 当前 patch 分支名称。
        :param evidence: resume 决策文件写入的确认依据。
        :return: 首跑、patch plan、resume 和 run_dir 的证据字典。
        """

    # 每个分支复制自己的 RTL，避免两个分支互相影响。
    path_local = _copy_fixture(source, case_root / branch_name)  # 分支本地 RTL 副本

    # verify run 目录按分支隔离，保留首跑和 resume 的共同上下文。
    path_run_dir = case_root / f"{branch_name}-run"  # 分支 verify run 目录

    # 首跑 auto_apply 应对高风险类别降级为确认。
    dict_first = verify_existing_verilog(  # patch library 首跑结果
        path_local,  # 首跑使用的分支本地 RTL 副本
        out_dir=path_run_dir,  # 首跑与 resume 共享的 run 目录
        spec_source=spec_source,  # patch-library 规格输入
        automation_mode="auto_apply",  # 触发高风险类别的自动降级策略
        tb_mode="generate",  # 生成测试平台以覆盖 patch 后验证
        tb_language="verilog",  # testbench 语言保持 Verilog
        readiness="static",  # 只运行静态准备度路径
        run_external=False,  # eval 不调用外部仿真工具
    )

    # 首跑 verification_result 记录策略和补丁类别。
    dict_first_payload = _read_json(  # 首跑 verification_result JSON 对象
        Path(dict_first["verification_result_path"])  # 首跑 verification_result 路径
    )

    # 首跑 patch plan 用于判断控制/时序类别是否被正确识别。
    dict_first_plan = _read_json(Path(dict_first["rtl_patch_plan_path"]))  # 首跑 RTL patch 计划 JSON 对象

    # 写入确认决策后 resume 同一 run 目录。
    path_decision = _write_decision(  # 分支确认决策文件
        path_run_dir / "decision.json",  # resume 读取的确认决策文件
        evidence=evidence,  # 模拟用户确认 patch 的证据文本
    )

    # resume 阶段验证确认决策是否能应用首跑生成的 patch。
    dict_resumed = verify_existing_verilog(  # 确认决策后重新运行 verify-existing 的证据包
        path_local,  # resume 继续使用同一份分支 RTL
        out_dir=path_run_dir,  # resume 复用首跑 run 目录
        spec_source=spec_source,  # resume 阶段沿用原始规格输入
        automation_mode="auto_apply",  # 保持与首跑一致的自动应用模式
        tb_mode="generate",  # resume 后仍生成回归测试平台
        tb_language="verilog",  # resume 生成的测试平台保持 Verilog
        decision_source=path_decision,  # 人工确认决策来源
        readiness="static",  # resume 保持静态验证范围
        run_external=False,  # resume 阶段仍禁用外部工具
    )

    # resume payload 确认最终是否应用并生成回归产物。
    path_resumed_result = Path(dict_resumed["verification_result_path"])  # resume verification_result JSON 文件路径

    # payload 内容用于后续检查 rtl_mutation.applied 和 post_apply 工件。
    dict_resumed_payload = _read_json(path_resumed_result)  # patch 应用状态和后验证文件检查依据

    # 返回分支证据，调用方统一做检查。
    return {
        "first_payload": dict_first_payload,  # 首跑验证策略与 mutation 摘要
        "first_plan": dict_first_plan,  # 首跑生成的 RTL patch 计划
        "first_run_dir": dict_first["run_dir"],  # 首跑 run_dir 字符串
        "resumed_payload": dict_resumed_payload,  # resume 后的 verification_result JSON
        "resumed_run_dir": dict_resumed["run_dir"],  # resume 复用的 run_dir 字符串
    }

# 判断高风险 patch 是否被降级到确认。
def _confirm_before_apply(dict_payload: dict[str, Any]) -> bool:
    """检查 rtl_mutation 是否为确认后应用策略。

        :param dict_payload: verify-existing 首跑 verification_result。
        :return: mutation policy 为 confirm_before_apply 且未应用时返回 True。
        """

    # rtl_mutation.policy 是自动降级的可观察字段。
    dict_mutation = _optional_nested_dict(dict_payload, "rtl_mutation")  # 首跑 RTL 修改策略摘要

    # 首跑应不应用 patch 且策略为 confirm_before_apply。
    return (
        dict_mutation.get("policy") == "confirm_before_apply"
        and not bool(dict_mutation.get("applied"))
    )

# 判断 resume 是否应用 patch 并写出后验证产物。
def _resume_applied_with_regression(dict_branch: dict[str, Any]) -> bool:
    """检查确认 resume 后的应用状态和 post-apply 回归产物。

        :param dict_branch: patch-library 单分支首跑和 resume 证据。
        :return: patch 已应用且后验证/等价产物都存在时返回 True。
        """

    # resume payload 记录最终 RTL mutation 状态。
    dict_payload = dict_branch["resumed_payload"]  # resume 后的 RTL 修改结果摘要

    # run 目录用于检查 post-apply 回归产物。
    path_run_dir = Path(dict_branch["resumed_run_dir"])  # 确认恢复后的运行目录

    # 应用成功且 post_apply_validation/equivalence 同时存在才算闭环。
    return (
        bool(dict_payload.get("rtl_mutation", {}).get("applied"))
        and Path(path_run_dir, "post_apply_validation.json").exists()
        and Path(path_run_dir, "post_apply_equivalence.json").exists()
    )

# baseline prompt 故意只包含通用 Verilog 需求。
def _render_baseline_prompt(dict_spec: dict[str, Any]) -> str:
    """渲染不含本 skill 结构化上下文的 baseline prompt。

        :param dict_spec: 输入规格 JSON 对象。
        :return: 通用 Verilog baseline prompt 文本。
        """

    # baseline 用于和 with-skill prompt 做同一组 expectation checks。
    return (
        "# Generic Verilog prompt\n\n"
        "Generate synthesizable Verilog-2001 RTL from this JSON spec.\n\n"
        "```json\n"
        + json.dumps(dict_spec, indent=2, ensure_ascii=False)
        + "\n```\n"
    )

# 语义门禁 fixture spec 与 tests/test_verilog_gate_catalog.py 保持一致。
def _rtl_md_fixture_spec() -> dict[str, Any]:
    """返回 RTL Markdown 约束评估使用的最小 RTL spec。

    :param 无: 不接收外部业务参数，fixture 内容在函数内固定。
    :return: lint fixture 共享的最小 RTL 规格字典。
    """

    # 该 spec 只用于 lint fixture，不进入真实模型调用。
    return {
        "name": "good_constraints",
        "description": "RTL Markdown constraint evaluation fixture.",
        "behavior": ["Register one input bit."],
        "constraints": [],
        "notes": [],
        "clock": {"name": "clk", "edge": "posedge"},
        "reset": {"name": "rst_n", "active": "low", "synchronous": False},
        "interfaces": {
            "ports": [
                {"name": "clk", "direction": "input", "width": 1, "role": "clock"},
                {"name": "rst_n", "direction": "input", "width": 1, "role": "reset"},
                {"name": "a", "direction": "input", "width": 4},
                {"name": "y", "direction": "output", "width": 1},
            ]
        },
        "outputs": [
            {"path": "rtl/good_constraints.v", "kind": "source", "language": "verilog"}
        ],
    }

# 违规 fixture 覆盖高置信 lint 规则。
def _rtl_md_bad_fixture() -> str:
    """返回故意违反 RTL Markdown 约束的 Verilog fixture。

    :param 无: 不接收外部业务参数，违规 fixture 文本在函数内固定。
    :return: 预期触发高置信 lint issue 的 Verilog 文本。
    """

    # 字符串逐行组织，便于 lint 规则定位问题行。
    return "\n".join(
        [
            "module bad_constraints (",
            "    input wire clk,",
            "    input wire rst_n,",
            "    input wire [3:0] a,",
            "    output reg y",
            ");",
            "wire gated_clk = clk & rst_n;",
            "initial y = 1'b0;",
            "always @(a || rst_n) begin",
            "  if (a == 4'bx) begin",
            "    y <= 1'b1;",
            "  end",
            "  case (4'b0000)",
            "    4'b0001: y = 1'b1;",
            "  endcase",
            "end",
            "always @(*) begin",
            "  if (a[0]) begin",
            "    y = 1'b0;",
            "  end",
            "end",
            "endmodule",
            "",
        ]
    )

# 合规 fixture 用于确认 lint 不误报。
def _rtl_md_clean_fixture() -> str:
    """返回满足 RTL Markdown 约束的 Verilog fixture。

    :param 无: 不接收外部业务参数，合规 fixture 文本在函数内固定。
    :return: 预期不触发 lint issue 的 Verilog 文本。
    """

    # 该 fixture 只包含基本时序逻辑，预期 lint issue 为空。
    return "\n".join(
        [
            "module good_constraints (",
            "    input wire clk,",
            "    input wire rst_n,",
            "    input wire [3:0] a,",
            "    output reg y",
            ");",
            "always @(posedge clk or negedge rst_n) begin",
            "  if (!rst_n) begin",
            "    y <= 1'b0;",
            "  end else begin",
            "    y <= a[0];",
            "  end",
            "end",
            "endmodule",
            "",
        ]
    )

# pass count 是多个 comparison 字段的共同逻辑。
def _pass_count(dict_checks: dict[str, bool]) -> int:
    """统计检查字典中通过项数量。

        :param dict_checks: 检查项名称到布尔结果的映射。
        :return: 值为 True 的检查项数量。
        """

    # True 值代表单项检查通过。
    return sum(
        1
        for bool_item in dict_checks.values()
        if bool_item
    )

# 目录切换上下文保持显式类形态，避免装饰器干扰 current-project 函数门禁。
class PushdContext:
    """临时切换当前工作目录的上下文管理器。"""

    # 初始化阶段只保存目标目录，进入 with 时再修改进程工作目录。
    def __init__(self, path: Path) -> None:
        """保存待切换的目标目录。

        :param path: with 块执行期间切换到的工作目录。
        :return: 无返回值，仅保存上下文管理器状态。
        """

        # 目标目录由调用方传入，保持旧 workflow 的相对路径语义。
        self.path = path  # with 块期间的目标工作目录

        # 进入 with 之前尚未知道原工作目录。
        self.path_previous: Path | None = None  # with 入口前的工作目录

    # 进入上下文时切换目录，返回 None 保持旧 contextmanager yield 行为。
    def __enter__(self) -> None:
        """进入 with 块并切换到目标目录。

        :param 无: 使用初始化时保存的目标目录。
        :return: 无业务返回值，with 块内不绑定额外对象。
        """

        # 记录进入前目录，退出时必须恢复。
        self.path_previous = Path.cwd()  # 调用方原始工作目录

        # 进入 skill 根目录，保持旧 workflow 相对路径行为。
        os.chdir(self.path)

        # 保持原先 contextmanager yield None 的行为。
        return None

    # 退出上下文时恢复目录，不吞掉 with 块内异常。
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """退出 with 块并恢复原工作目录。

        :param exc_type: with 块抛出的异常类型；正常退出时为 None。
        :param exc_value: with 块抛出的异常对象；正常退出时为 None。
        :param traceback: with 块异常的 traceback；正常退出时为 None。
        :return: 始终返回 False，让调用方继续感知原异常。
        """

        # 未进入上下文时不应调用退出逻辑，保守返回不吞异常。
        if self.path_previous is None:

            # 没有可恢复目录时，保留异常传播语义。
            return False

        # 恢复目录放在退出路径中，避免异常污染后续 eval。
        os.chdir(self.path_previous)

        # 返回 False 表示不吞掉 with 块中的异常。
        return False

# 切换工作目录以兼容依赖相对 skill 根的 legacy workflow。
def _pushd(path: Path) -> PushdContext:
    """创建临时切换工作目录的上下文管理器。

    :param path: with 块执行期间切换到的工作目录。
    :return: 可用于 with 语句的目录切换上下文管理器。
    """

    # 返回对象保持 with _pushd(path) 的既有调用形式。
    return PushdContext(path)

