"""Verilog skill 的确定性有效性评估入口。"""

# 未来注解保持运行时导入轻量，便于 CLI 与测试复用。
from __future__ import annotations

# 标准库负责临时目录、JSON 报告和路径上下文管理。
import json
import re

# 类型工具仅用于内部 evaluator 签名表达。
from collections.abc import Callable
from pathlib import Path
from typing import Any

# 公开 facade API 的生成与分析入口。
from scripts.python.facade.verilog_api import (
    analyze_existing_verilog,
    compare_verilog_semantics,
    improve_existing_verilog,
    render_verilog_prompt,
)

# 公开 facade API 的 routing 与 workflow 入口。
from scripts.python.facade.verilog_api import (
    route_verilog_request,
    run_verilog_batch,
    run_verilog_workflow,
)

# 质量门和 verify-repair API 覆盖最终工件验证与已有 RTL 修复。
from scripts.python.facade.verilog_api import (
    validate_verilog_artifacts,
    verify_existing_verilog,
)

# 本地 runtime helper 提供固定 skill 根、模板摘要和 lint 证据。
from scripts.python.workflow.config import skill_root
from scripts.python.workflow.pattern_templates import summarize_pattern_templates
from scripts.python.workflow.rtl_md_constraints import (
    load_rtl_md_constraints,
    summarize_constraints_for_prompt,
)
from scripts.python.quality.static_lint import lint_generated_rtl
from scripts.python.workflow.workspace import write_json

# case 基础工具负责最核心的检查计算与文件复制。
from .skill_effectiveness_support import (
    _case_expectations,
    _cleanup_temp_root,
    _confirm_before_apply,
    _comparison_from_baseline,
    _copy_blocked_sources,
    _copy_fixture,
)

# case 基础工具还提供远程摘要与期望命中计算。
from .skill_effectiveness_support import (
    _enabled_checks,
    _evaluate_remote_runs,
    _expectation_checks,
    _local_cases_passed,
)

# 可选输入解析 helper 负责从 eval case 中提取附加上下文。
from .skill_effectiveness_support import (
    _optional_dict,
    _optional_nested_dict,
    _optional_skill_path,
)

# 汇总与工件目录 helper 负责统一报告结构和运行目录。
from .skill_effectiveness_support import (
    _overall_summary,
    _pass_count,
    _prepare_case_root,
    _pushd,
    _read_json,
)

# 汇总 helper 还提供 baseline prompt 和 resume 回归判断。
from .skill_effectiveness_support import (
    _render_baseline_prompt,
    _report_from_checks,
    _resume_applied_with_regression,
    _temporary_eval_root,
)

# routing 输入 helper 负责日志、产物目录和只读入口上下文。
from .skill_effectiveness_support import (
    _routing_artifact_dir,
    _routing_log_paths,
    _routing_source_path,
)

# RTL Markdown 与 patch-library helper 负责夹具和确认式分支。
from .skill_effectiveness_support import (
    _rtl_md_bad_fixture,
    _rtl_md_clean_fixture,
    _rtl_md_fixture_spec,
    _run_patch_library_branch,
    _write_decision,
)

# 固定 skill 根用于把 evals.json 中的相对路径解析到可发布 skill 主体。
SKILL_ROOT = skill_root()  # skill 主体根目录

# 单个 eval case 的内部执行函数签名，保持 _evaluate_case 分派逻辑可读。
CaseEvaluator = Callable[[dict[str, Any], str, Path], dict[str, Any]]  # eval kind 处理函数类型

# 公开 CLI 入口读取 eval 集并写出汇总报告。
def evaluate_skill_effectiveness(
    evals_path: Path,
    out_path: Path,
    *,
    remote_runs_report: dict[str, Any] | None = None,
    require_remote: bool = False,
) -> dict[str, Any]:
    """执行 skill-effectiveness evals 并生成兼容旧字段的报告。

    参数:
        evals_path: 指向 `evals.json` 的路径，内部必须提供非空 `cases` 列表。
        out_path: 顶层聚合报告的写出路径。
        remote_runs_report: 可选的远程 retained-run 摘要，供总通过判定复用。
        require_remote: 是否把远程 retained-run 结果纳入强制门禁。

    返回:
        与写入 `out_path` 相同的一份聚合报告字典，便于测试直接断言。

    异常:
        ValueError: 当 `evals.json` 缺少非空 `cases` 列表时抛出。
    """

    # evals 文件必须先解析为对象，后续才能区分空集合与格式错误。
    dict_payload = json.loads(evals_path.read_text(encoding="utf-8"))  # evals.json 顶层内容

    # cases 字段是 CLI 和 validate_verilog_skill.py 共同依赖的稳定输入。
    list_cases = dict_payload.get("cases")  # 待执行的评估用例列表

    # 阻止空 eval 集被误判为验证通过。
    if not isinstance(list_cases, list) or not list_cases:

        # 报错信息保留原路径，方便调用者定位损坏的 evals.json。
        raise ValueError(
            f"> ERR: [Python] Skill eval cases must be a non-empty list: {evals_path}"
        )

    # 每次运行使用独立目录，避免并行 smoke 互相覆盖工件。
    path_temp_root = _temporary_eval_root()  # 本轮 skill-effectiveness 临时根

    # 运行结束后清理本轮目录，同时保留并行 worker 的兄弟目录。
    try:

        # Verilog workflow 里有相对 skill 根的资源读取，进入 skill 根可保持旧行为。
        with _pushd(SKILL_ROOT):

            # 按 evals.json 原始顺序累计每个 case 的执行结果。
            list_case_reports: list[dict[str, Any]] = []  # 本轮所有 case 的详细报告

            # 逐个运行 case，便于失败时回溯到具体输入项。
            for dict_case in list_cases:

                # 把当前 case 的完整报告追加到聚合列表末尾。
                list_case_reports.append(_evaluate_case(dict_case, path_temp_root))

        # 计算远程 retained-run 的摘要，供顶层门禁复用。
        dict_remote_report = _evaluate_remote_runs(  # 远程验证证据的聚合摘要
            remote_runs_report,  # 上游 retained-run 收集到的远程证据
            require_remote=require_remote,  # 当前回归是否把远程校验设为硬门禁
        )

        # 汇总本地 case 的总体通过结论。
        bool_local_ok = _local_cases_passed(list_case_reports)  # 本地 case 的总通过状态

        # 默认先假定远程门禁不影响本地结论。
        bool_remote_gate_ok = True  # 远程门禁的初始放行状态

        # 只有显式要求远程，或确实拿到远程摘要时，才采纳远程门禁结论。
        if require_remote or dict_remote_report["checked"]:

            # 远程摘要中的 ok 字段决定远程门禁是否通过。
            bool_remote_gate_ok = bool(dict_remote_report["ok"])  # 远程门禁的实际判定状态

        # 生成兼容 validate_verilog_skill.py 的顶层 summary 结构。
        dict_summary = _overall_summary(  # 顶层 summary 的计数与远程标记
            list_case_reports,  # 本地 case 的完整执行报告
            dict_remote_report,  # 远程 retained-run 的聚合摘要
            require_remote=require_remote,  # 是否要求远程结论参与最终 ok
        )

        # ok 字段整合本地 case 与远程必需状态。
        dict_summary["ok"] = bool_local_ok and bool_remote_gate_ok  # 总体通过状态

        # 顶层报告字段保持 version、cases、remote、summary 的旧兼容形状。
        dict_report = {
            "version": 1,  # 报告格式版本
            "evals_path": str(evals_path),  # 本次读取的 evals.json 路径
            "cases": list_case_reports,  # 所有 case 的详细报告
            "remote": dict_remote_report,  # 本轮远程 retained-run 的汇总证据
            "summary": dict_summary,  # 顶层通过计数和 ok 状态
        }  # CLI 写出的 skill-effectiveness 报告

        # write_json 负责统一缩进和 UTF-8 写出。
        write_json(out_path, dict_report)

        # 返回内存中的同一份报告，供单元测试无需重新读文件。
        return dict_report

    # 无论前面的 case 是否失败，都要回收本轮临时目录。
    finally:

        # 清理只针对本轮 pid/timestamp 目录，不碰其他 worker 的 smoke 输出。
        _cleanup_temp_root(path_temp_root)

# 公开给测试直接调用的单 case 分派入口。
def _evaluate_case(case: dict[str, Any], temp_root: Path) -> dict[str, Any]:
    """根据 eval case kind 调用对应的确定性检查流程。

    参数:
        case: 单个 eval case 的配置字典，至少需要可定位的 `id` 字段。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        单个 case 的完整报告字典，结构与历史 skill-effectiveness 输出保持兼容。

    异常:
        ValueError: 当 case 缺少可写目录所需的 `id` 时抛出。
    """

    # id 进入路径和报告字段，因此先统一转成字符串。
    str_case_id = str(case.get("id") or "")  # eval case 的稳定标识

    # 缺少 id 的用例无法安全写目录，也无法在报告中定位。
    if not str_case_id:

        # 抛出完整 case 内容，便于维护 evals.json。
        raise ValueError(f"> ERR: [Python] Eval case is missing id: {case}")

    # kind 缺失时走默认 prompt/workflow regression，保持旧行为。
    str_kind = str(case.get("kind") or "")  # 用于选择 evaluator 的 case kind

    # 已登记 kind 使用专属 evaluator，默认分支验证生成 prompt 和 workflow。
    if str_kind in CASE_EVALUATORS:

        # 专属 evaluator 负责保持该 case 的历史报告字段。
        return CASE_EVALUATORS[str_kind](case, str_case_id, temp_root)

    # 默认 case 覆盖 prompt、requirements、codegen plan 与静态 validation。
    return _evaluate_default_generation_case(case, str_case_id, temp_root)

# 默认生成链路 case 验证 use-case/improved-template 注入效果。
def _evaluate_default_generation_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估常规 Verilog 生成链路相对 baseline prompt 的有效性。

    参数:
        case: 默认生成回归的 case 配置，内部需要提供 `spec` 和期望集合。
        str_case_id: 已归一化的 case 标识，用于工件目录和报告输出。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        包含 with-skill、without-skill、comparison 和模板摘要的默认生成报告。

    异常:
        无显式抛出的业务异常；底层文件读取或 workflow 调用异常按原样向上传播。
    """

    # 先定位默认生成回归要读取的 spec 文件。
    path_spec = SKILL_ROOT / str(case["spec"])  # eval case 指向的 spec 文件

    # 解析 spec 内容，供 prompt、workflow 和 validation 共用。
    dict_spec = _read_json(path_spec)  # Verilog 生成规格

    # 为当前 case 准备隔离的工件输出根目录。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # 当前 case 工件根

    # 指定 with-skill prompt 的证据文件路径。
    path_prompt = path_case_root / "with-skill-prompt.md"  # with-skill prompt 输出路径

    # 渲染真实模板注入后的 prompt 文本。
    str_prompt = render_verilog_prompt(dict_spec, path_prompt)["prompt"]  # 模板注入后的 prompt 文本

    # 运行默认生成 workflow，收集 staged 流程产物。
    dict_workflow = run_verilog_workflow(  # 常规 staged workflow 的执行结果
        dict_spec,  # 当前默认生成 case 的完整规格
        out_dir=path_case_root / "workflow",  # workflow 工件输出目录
        provider_name="mock",  # 离线回归使用的 provider 名称
        readiness="static",  # 本轮只做静态准备度门禁
        run_external=False,  # 禁止拉起外部工具链
    )

    # 取出 workflow 最后一轮 attempt，定位最终 RTL 输出目录。
    dict_attempt = dict_workflow["workflow_result"]["attempts"][-1]  # workflow 最后一轮尝试记录

    # 拼出静态验证要读取的 generated RTL 目录。
    path_generated_dir = (  # 静态验证要读取的 generated RTL 目录
        path_case_root  # 当前 case 的隔离工件根
        / "workflow"  # staged workflow 输出根目录
        / dict_attempt["attempt_id"]  # 最后一轮 attempt 的目录名
        / "rtl"  # RTL 阶段输出目录
        / "generated"  # 最终生成源码所在目录
    )

    # 对最终生成工件运行静态 validation 门禁。
    dict_validation = validate_verilog_artifacts(  # 生成工件静态验证报告
        dict_spec,  # 与生成阶段一致的规格输入
        path_generated_dir,  # 待验证的生成 RTL 目录
        run_external=False,  # 静态回归不启动外部验证器
        readiness="static",  # 保持静态准备度校验模式
    )

    # 读取 requirements 阶段产物，确认模板选择结果。
    dict_requirements = _read_json(Path(dict_workflow["requirements_path"]))  # requirements 阶段的模板选择记录

    # 读取 codegen plan 产物，保留 checkpoint 与准备度信息。
    dict_codegen_plan = _read_json(Path(dict_workflow["codegen_plan_path"]))  # codegen plan 阶段的规划产物

    # 提取当前默认生成 case 声明的命中条件。
    dict_expectations = _case_expectations(case)  # 默认生成链路的期望集合

    # 用真实 prompt 与中间产物计算 with-skill 期望命中情况。
    dict_with_checks = _expectation_checks(  # 启用模板后的命中结果
        prompt=str_prompt,  # 实际渲染出的 with-skill prompt
        requirements=dict_requirements,  # requirements 阶段记录的模板选择
        codegen_plan=dict_codegen_plan,  # 规划阶段产出的 checkpoint 信息
        pattern_templates=dict_requirements.get("selected_pattern_template_ids", []),  # 已选模式模板列表
        expectations=dict_expectations,  # 当前 case 期望命中的条件集合
    )

    # 用剥离模板上下文的 baseline prompt 生成对照结果。
    dict_baseline_checks = _expectation_checks(  # baseline 对照组的命中结果
        prompt=_render_baseline_prompt(dict_spec),  # 剥离模板上下文后的 prompt
        requirements={"selected_use_case_template_id": None},  # baseline 不记录模板命中
        codegen_plan={},  # baseline 不提供规划产物
        pattern_templates=[],  # baseline 不注入模式模板
        expectations=dict_expectations,  # 与 with-skill 复用同一组检查条件
    )

    # 计算模板链路相对 baseline 的净收益，供最终 passed 判定使用。
    dict_comparison = _comparison_from_baseline(  # with-skill 与 baseline 的净收益对比
        dict_with_checks,  # 模板链路的命中明细
        dict_baseline_checks,  # 剥离模板后的基线命中明细
    )

    # 计算 with-skill 链路是否同时满足 workflow、validation 和零警告。
    bool_stable = (
        dict_workflow["status"] == "passed"  # workflow 主流程整体通过
        and bool(dict_validation.get("ok"))  # 静态验证返回 ok
        and dict_validation.get("warnings") == 0  # 静态验证没有遗留 warning
    )  # 默认生成链路的稳定状态

    # 组装 with-skill 证据块，保留 prompt、模板和 validation 信息。
    dict_with_skill = {
        "prompt_path": str(path_prompt),  # with-skill prompt 证据文件
        "stable": bool_stable,  # workflow 和 validation 共同确认的稳定性
        "selected_use_case_template_id": dict_requirements.get("selected_use_case_template_id"),  # 选中的板级模板
        "selected_pattern_template_ids": dict_requirements.get("selected_pattern_template_ids", []),  # 选中的模式模板
        "expectation_checks": dict_with_checks,  # with-skill 期望检查结果
        "validation": {  # 静态 validation 的结果摘要
            "ok": dict_validation.get("ok"),  # 静态验证是否通过
            "warnings": dict_validation.get("warnings"),  # 静态验证警告数
        },
    }  # with-skill 报告主体

    # baseline 侧只保留未启用模板时的原始命中证据。
    dict_without_skill = {
        "selected_use_case_template_id": None,  # baseline 不选择 use-case 模板
        "selected_pattern_template_ids": [],  # baseline 恒为空的模式模板集合
        "expectation_checks": dict_baseline_checks,  # baseline prompt 的期望检查
    }  # without-skill baseline 摘要

    # 判断默认生成 case 是否同时稳定、命中期望且优于 baseline。
    bool_passed = (
        bool_stable  # workflow 与 validation 都保持稳定
        and all(dict_with_checks.values())  # with-skill 期望项全部命中
        and dict_comparison["improved"]  # 相对 baseline 至少有净改进
    )  # 默认生成 case 是否通过

    # 返回字段保持旧 skill-effectiveness 报告形状。
    return {
        "id": str_case_id,  # eval case 标识
        "kind": case.get("kind"),  # eval 用例分类
        "spec": str(case["spec"]),  # 输入 spec 相对路径
        "passed": bool_passed,  # 当前默认生成回归是否整体通过
        "with_skill": dict_with_skill,  # with-skill 证据块
        "without_skill": dict_without_skill,  # baseline 对照块
        "comparison": dict_comparison,  # with/baseline 通过项对比
        "pattern_templates": summarize_pattern_templates(dict_spec),  # 规格命中的模式模板摘要
    }

# existing-RTL 分析 case 检查结构理解产物是否覆盖预期元素。
def _evaluate_analysis_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 analyze_existing_verilog 的结构抽取能力。

    参数:
        case: 分析回归配置，提供待分析 RTL 和可选规格说明。
        str_case_id: 当前分析回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录结构拆分、特征映射和状态元素命中情况的分析报告。

    异常:
        无显式抛出的业务异常；底层文件读取和分析 facade 异常按原样向上传播。
    """

    # 解析 analysis 回归要读取的 RTL 夹具路径。
    path_source = SKILL_ROOT / str(case["source"])  # 参与结构分析的 RTL 夹具

    # 解析可选规格说明路径，为结构分析提供附加上下文。
    path_spec_source = _optional_skill_path(case, "spec_source")  # 辅助分析的可选规格说明

    # 为 analysis 回归准备独立工件目录。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # 分析回归的独立工件目录

    # analysis 目录专门保存结构拆分 JSON 和 feature/state 映射证据。
    dict_result = analyze_existing_verilog(  # existing RTL 分析输出路径集合
        path_source,  # 待分析的 RTL 输入夹具
        out_dir=path_case_root / "analysis",  # analysis JSON 与映射证据的输出目录
        spec_source=path_spec_source,  # 可选规格说明输入
    )

    # 读取 analysis JSON，作为后续断言的统一事实源。
    dict_analysis = _read_json(Path(dict_result["analysis_path"]))  # 结构分析 JSON 的完整内容

    # 提取 analysis 回归声明的结构命中条件。
    dict_expectations = _case_expectations(case)  # analysis 回归启用的期望集合

    # 计算最少拆分候选数量阈值。
    int_decomposition_min = int(  # decomposition 的最小命中阈值
        dict_expectations.get("decomposition_candidates_min", 0)  # case 声明的最小候选数
    )

    # 取出需要命中的 feature 名称列表。
    list_mapped_features = list(dict_expectations.get("mapped_features", []))  # 需要命中的 feature 名称

    # 取出需要命中的状态元素名称列表。
    list_expected_states = list(dict_expectations.get("state_elements", []))  # 需要命中的状态元素名称

    # 汇总 analysis 中识别出的 feature 名称集合。
    set_feature_names = {  # feature_mappings 中提取出的名称集合
        dict_item["name"]  # 单个 feature mapping 的名称字段
        for dict_item in dict_analysis.get("feature_mappings", [])  # 遍历分析结果里的 feature 映射
    }

    # 把 state_elements 转成名称集合，便于和期望状态名直接比对。
    set_state_names = {  # analysis 报告里抽取出的状态名集合
        dict_item["name"]  # 单个状态元素的名称字段
        for dict_item in dict_analysis.get("state_elements", [])  # 遍历分析结果里的状态元素
    }

    # 检查结果分别对应拆分候选、feature 映射和状态元素覆盖率。
    dict_checks = {
        "decomposition_candidates_min": (  # 拆分候选数量是否达到阈值
            len(dict_analysis.get("decomposition_candidates", []))  # 实际识别出的候选数量
            >= int_decomposition_min  # case 期望的最小数量门槛
        ),
        "mapped_features": all(  # 预期 feature 是否都已建立映射
            str_item in set_feature_names  # 当前期望 feature 是否出现在分析结果中
            for str_item in list_mapped_features  # 遍历 case 声明的 feature 列表
        ),
        "state_elements": all(  # 预期状态元素是否都出现在分析结果中
            str_item in set_state_names  # 当前状态元素是否已被分析识别
            for str_item in list_expected_states  # 遍历 case 声明的状态元素
        ),
    }  # 分析 case 的期望检查

    # 分析 case baseline 不运行旧工具，默认 without 全 false。
    return _report_from_checks(
        case,
        str_case_id,
        dict_checks,
        source=str(case["source"]),
        with_skill_extra={"analysis_path": str(dict_result["analysis_path"])},
    )

# RTL Markdown 约束 case 检查 lint、prompt 摘要与规则目录一致性。
def _evaluate_rtl_md_constraint_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 RTL Markdown 约束是否进入 prompt 和静态 lint。

    参数:
        case: RTL Markdown 约束回归配置，描述 blocked code 和目录期望。
        str_case_id: 当前约束回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录违规夹具、合规夹具、约束目录和 prompt 摘要状态的报告。

    异常:
        无显式抛出的业务异常；底层文件写入、lint 或约束加载异常按原样向上传播。
    """

    # 为 RTL Markdown 回归准备共享工件根目录。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # 约束回归的工件根目录

    # 违规夹具会写入 generated 目录，触发阻断规则。
    path_generated = path_case_root / "generated"  # 写入违规 RTL 的夹具目录

    # 合规夹具会写入 clean 目录，验证规则不会误杀。
    path_clean_root = path_case_root / "clean"  # 写入合规 RTL 的夹具目录

    # 创建两个 fixture 目录，隔离 lint 输入。
    path_generated.mkdir(parents=True, exist_ok=True)

    # clean 目录单独创建，用来承载不应命中规则的对照夹具。
    path_clean_root.mkdir(parents=True, exist_ok=True)

    # 写入违规 RTL，用来触发高置信 MUST/REC 规则。
    (path_generated / "bad_constraints.v").write_text(
        _rtl_md_bad_fixture(),
        encoding="utf-8",
    )

    # 写入 testbench 名称变体，覆盖 lint 对 testbench 文件的处理。
    (path_generated / "good_constraints_tb.v").write_text(
        _rtl_md_clean_fixture().replace("good_constraints", "good_constraints_tb"),
        encoding="utf-8",
    )

    # 写入合规 RTL，确认规则不会误杀正常结构。
    (path_clean_root / "good_constraints.v").write_text(
        _rtl_md_clean_fixture(),
        encoding="utf-8",
    )

    # 生成 lint 所需的夹具规格描述。
    dict_spec = _rtl_md_fixture_spec()  # lint 所需的 RTL Markdown 夹具规格

    # 对违规夹具运行 lint，收集命中的阻断问题。
    list_blocked_issues = lint_generated_rtl(dict_spec, path_generated)  # 违规 fixture lint 结果

    # 汇总违规夹具命中的规则码，供逐条断言使用。
    set_blocked_codes = {  # 违规夹具实际命中的规则码集合
        obj_issue.code  # 单条 lint issue 的规则码
        for obj_issue in list_blocked_issues  # 遍历 bad fixture 的全部阻断问题
    }

    # 对 clean 夹具运行 lint，确认正常结构不会被误杀。
    list_clean_issues = lint_generated_rtl(dict_spec, path_clean_root)  # 合规 fixture lint 结果

    # 读取约束目录，核对规则总量和规则 id。
    dict_catalog = load_rtl_md_constraints()  # RTL Markdown 约束目录

    # 读取注入 prompt 的约束摘要文本。
    str_prompt_summary = summarize_constraints_for_prompt()  # prompt 中注入的约束摘要

    # 把 catalog 中登记的规则 id 拉平成集合，后面用于 prompt 覆盖检查。
    set_catalog_rule_ids = {  # 约束目录中声明的全部规则 id 集合
        str(dict_rule["id"])  # 目录中单条规则的稳定 id
        for dict_rule in dict_catalog["rules"]  # 逐条读取 catalog 规则记录里的 id
    }

    # 提取 RTL Markdown 回归要验证的条件集合。
    dict_expectations = _case_expectations(case)  # 约束回归启用的期望集合

    # 动态期望会按规则码展开成多条可诊断的检查项。
    dict_checks: dict[str, bool] = {}  # 约束 case 的检查集合

    # 每个期望阻断码都必须在 bad fixture lint 中出现。
    for str_code in dict_expectations.get("blocked_codes", []):

        # key 中保留规则码，便于报告定位具体缺失规则。
        dict_checks[f"blocked_{str_code}"] = str(str_code) in set_blocked_codes  # 当前阻断码是否真实命中

    # 可选检查确认 clean fixture 没有 lint 问题。
    if dict_expectations.get("clean_has_no_issues"):

        # 合规 fixture 不应产生任何静态问题。
        dict_checks["clean_has_no_issues"] = not list_clean_issues  # clean fixture 是否零问题通过

    # 可选检查确认目录总数与 eval 期望一致。
    if dict_expectations.get("catalog_total_rules"):

        # 同时比较 total_rules 字段和实际 rules 长度。
        dict_checks["catalog_total_rules"] = (
            dict_catalog.get("total_rules")  # 目录声明的总规则数
            == dict_expectations["catalog_total_rules"]  # eval 侧期望的规则数
            == len(dict_catalog.get("rules", []))  # rules 列表的实际长度
        )

    # 可选检查确认 prompt 摘要没有漏掉规则 id。
    if dict_expectations.get("prompt_mentions_all_rules"):

        # 所有 catalog id 都应出现在 prompt summary 中。
        dict_checks["prompt_mentions_all_rules"] = all(  # prompt 摘要是否覆盖全部规则 id
            str_rule_id in str_prompt_summary  # 当前规则 id 是否出现在 prompt 摘要中
            for str_rule_id in set_catalog_rule_ids  # 逐条遍历 catalog 中声明的规则 id
        )

    # 可选检查确认 lint 消息携带 MUST/REC 规则名。
    if dict_expectations.get("static_issues_include_rule_ids"):

        # 每条阻断消息都应可追溯到具体约束规则。
        dict_checks["static_issues_include_rule_ids"] = all(  # 阻断消息是否都能追溯到规则码
            re.search(r"(MUST|REC)_[A-Z0-9_]+", obj_issue.message)  # 消息中是否带可追溯的规则码
            for obj_issue in list_blocked_issues  # 逐条检查 bad fixture 的 lint 消息
        )

    # 没有显式期望时提供基础 smoke 语义。
    if not dict_checks:

        # 默认至少要求违规 fixture 被拦截且 clean fixture 放行。
        dict_checks = {
            "blocked_any": bool(set_blocked_codes),  # bad fixture 是否至少命中一条阻断规则
            "clean_has_no_issues": not list_clean_issues,  # clean fixture 是否保持零问题
        }

    # 约束 case 的 with_skill 需要附加 lint 证据字段。
    return _report_from_checks(
        case,
        str_case_id,
        dict_checks,
        with_skill_extra={
            "blocked_codes": sorted(set_blocked_codes),
            "clean_issue_count": len(list_clean_issues),
            "catalog_total_rules": dict_catalog.get("total_rules"),
        },
    )

# transform case 验证分析、脚手架、分区辅助和语义比较链路。
def _evaluate_transform_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 existing RTL transform assist 的核心产物。

    参数:
        case: transform 回归配置，提供输入 RTL 和启用的检查项。
        str_case_id: 当前 transform 回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录 testbench scaffold、partition wrapper 和语义比较结果的报告。

    异常:
        无显式抛出的业务异常；底层分析、refine 和语义比较异常按原样向上传播。
    """

    # 解析 transform 回归要读取的基准 RTL。
    path_source = SKILL_ROOT / str(case["source"])  # transform 基准 RTL 夹具

    # 为 transform 回归准备隔离工件目录。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # transform 回归工件目录

    # 先运行结构分析，为后续 tb scaffold 提供分析结果。
    dict_analysis = analyze_existing_verilog(  # transform 前置结构分析结果
        path_source,  # transform 输入的原始 RTL
        out_dir=path_case_root / "analysis",  # 结构分析工件目录
    )  # existing RTL 分析结果

    # 生成 testbench scaffold，验证 improve_goal 分支可用。
    dict_tb_result = improve_existing_verilog(  # tb scaffold 辅助结果
        path_source,  # 与 analysis 相同的 RTL 源文件
        out_dir=path_case_root / "tb",  # testbench scaffold 输出目录
        improve_goal="tb_scaffold",  # 生成测试平台脚手架
        analysis_source=dict_analysis["analysis_path"],  # 复用 analysis JSON 作为结构上下文
    )

    # 生成 partition wrapper，验证结构辅助分支能否给出包装骨架。
    dict_partition_result = improve_existing_verilog(  # 分区包装辅助的执行结果
        path_source,  # 待拆分包装的原始 RTL
        out_dir=path_case_root / "partition",  # partition_assist 的工件目录
        improve_goal="partition_assist",  # 请求输出分区包装与建议
    )

    # 复制一份等价候选 RTL，作为 compare-same 的输入。
    path_same_candidate = _copy_fixture(path_source, path_case_root, "same.v")  # 等价候选 RTL

    # 比较同源候选，确认语义比较不会误报。
    dict_same_compare = compare_verilog_semantics(  # 同源候选语义比较结果
        path_source,  # 基准 RTL 输入
        path_same_candidate,  # 与基准等价的候选副本
        out_dir=path_case_root / "compare-same",  # compare-same 工件目录
        run_external=False,  # 仅使用静态语义比较
    )

    # 指定宽度漂移候选的输出路径。
    path_drift_candidate = path_case_root / "drift.v"  # 非等价候选 RTL

    # 宽度漂移内容直接写入候选文件，供 compare_verilog_semantics 识别。
    path_drift_candidate.write_text(
        path_source.read_text(encoding="utf-8").replace(
            "output reg green",
            "output reg [1:0] green",
        ),
        encoding="utf-8",
    )

    # 比较宽度漂移候选，确认语义差异能被正确识别。
    dict_drift_compare = compare_verilog_semantics(  # 漂移候选语义比较结果
        path_source,  # 作为黄金参考的原始 RTL
        path_drift_candidate,  # 注入位宽漂移后的候选 RTL
        out_dir=path_case_root / "compare-drift",  # 宽度漂移对照的输出目录
        run_external=False,  # 只验证 comparator 的静态判断结果
    )

    # 提取 transform 回归要启用的断言开关。
    dict_expectations = _case_expectations(case)  # transform 分支启用的断言集合

    # 这组检查分别覆盖 testbench、wrapper 和语义对照三类工件。
    dict_checks = _enabled_checks(  # transform 回归要启用的检查集合
        {
            "tb_scaffold": Path(  # testbench scaffold 的存在性检查
                dict_tb_result["artifacts"].get("testbench", "")  # testbench 工件路径字符串
            ).exists(),  # testbench scaffold 工件是否写出
            "partition_wrapper": Path(  # 检查 partition 输出里是否真的落成 wrapper 文件
                dict_partition_result["artifacts"].get("wrapper", "")  # formatter partition 结果里记录的 wrapper 路径字符串
            ).exists(),  # partition wrapper 文件是否确实生成
            "compare_same_passes": dict_same_compare["status"] == "passed",  # 等价副本是否保持通过
            "drift_detected": dict_drift_compare["status"] == "failed",  # 宽度漂移候选是否被拦截
        },
        dict_expectations,  # 仅启用 style_improve case 明确声明的检查项
    )

    # transform case 返回 source 字段方便报告定位输入 RTL。
    return _report_from_checks(
        case,
        str_case_id,
        dict_checks,
        source=str(case["source"]),
        stable=True,
    )

# style_improve case 验证 Markdown 报告和 ready 状态。
def _evaluate_style_improve_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 style_improve 辅助是否产出可审查报告。

    参数:
        case: style_improve 回归配置，提供输入 RTL 和章节期望。
        str_case_id: 当前 style_improve 回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录 style report、章节存在性和 ready 状态的报告。

    异常:
        无显式抛出的业务异常；底层 refine 调用和报告读取异常按原样向上传播。
    """

    # 解析 style_improve 回归要读取的 RTL 夹具。
    path_source = SKILL_ROOT / str(case["source"])  # style_improve 输入 RTL 夹具

    # 为 style_improve 输出准备独立目录，便于单独核对 Markdown 报告。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # style_improve 隔离工件目录

    # style_improve 只生成辅助报告，不直接改写源文件。
    dict_result = improve_existing_verilog(  # style_improve 报告生成结果
        path_source,  # 需要审阅风格的 RTL 源文件
        out_dir=path_case_root / "style-improve",  # style_improve Markdown 的输出目录
        improve_goal="style_improve",  # 仅生成风格审阅报告
    )

    # 取出 style report 的文件路径，供章节检查使用。
    path_style_report = Path(dict_result["artifacts"]["style_report"])  # style report 工件路径

    # 读取 style report 正文，检查关键章节是否写出。
    str_style_text = path_style_report.read_text(encoding="utf-8")  # 用于查找固定章节标题的报告正文

    # 读取 style_improve 的 validation 记录，确认 ready 标记与 goal 没有漂移。
    dict_validation = _read_json(  # 核对 style_improve ready/goal 的 validation 记录
        Path(dict_result["transform_validation_path"])  # ready 状态对应的 validation 文件路径
    )

    # 读取 style_improve case 自己声明的章节与 ready 约束。
    dict_expectations = _case_expectations(case)  # style_improve case 打开的断言集合

    # style_improve 需要同时覆盖报告章节和 ready 状态一致性。
    dict_checks = _enabled_checks(  # style_improve 报告章节与 ready 一致性检查
        {
            "style_report_present": path_style_report.is_file(),  # style report 文件是否写出
            "preserve_section_present": "## Preserve" in str_style_text,  # 保留建议章节是否存在
            "suggested_improvements_present": "## Suggested style improvements" in str_style_text,  # 润色建议章节是否存在
            "ready_state_recorded": (  # ready 标记与目标模式的一致性检查
                bool(dict_validation.get("ready"))  # validation 是否记录 ready 标记
                and dict_validation.get("goal") == "style_improve"  # goal 字段是否仍指向 style_improve
            ),  # ready 标记与 goal 是否同时保持一致
        },
        dict_expectations,  # 仅启用 style_improve case 显式要求的章节与 ready 检查
    )

    # 报告沿用标准 checks 结构。
    return _report_from_checks(
        case,
        str_case_id,
        dict_checks,
        source=str(case["source"]),
    )

# checkpoint case 验证计划阶段结构化 checkpoint 和 prompt 注入。
def _evaluate_checkpoint_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 codegen plan 的 checkpoint 结构和 prompt 可见性。

    参数:
        case: checkpoint 回归配置，提供 spec 和启用的检查项。
        str_case_id: 当前 checkpoint 回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录结构化 checkpoint、依赖图和 python prompt 提示的报告。

    异常:
        无显式抛出的业务异常；planning、requirements 或 prompt 渲染异常按原样向上传播。
    """

    # 局部导入避免普通 eval 入口为 planning/requirements 增加 import 成本。
    from scripts.python.workflow.planning import decompose_spec
    from scripts.python.workflow.requirements import build_codegen_plan

    # spec 会同时驱动 codegen plan 生成和 python prompt 渲染。
    path_spec = SKILL_ROOT / str(case["spec"])  # checkpoint 回归使用的 spec 文件

    # 读取 spec，供 planning 和 prompt 渲染共用。
    dict_spec = _read_json(path_spec)  # 供 planning 流程消费的 spec 内容

    # checkpoint 需要同时落盘 plan 和 python prompt，因此单独隔离输出目录。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # 保存 checkpoint plan 与 prompt 的隔离目录

    # codegen plan 需要保留 checkpoint 与依赖图两类结构化字段。
    dict_plan = build_codegen_plan(decompose_spec(dict_spec))  # planning 阶段输出的 codegen plan

    # 渲染 python 阶段 prompt，确认 checkpoint 信息对下游可见。
    str_python_prompt = render_verilog_prompt(  # python 阶段最终注入的 prompt 文本
        dict_spec,  # 当前 checkpoint case 的规格输入
        path_case_root / "python-prompt.md",  # python 阶段 prompt 的落盘路径
        stage="python",  # 指定渲染 python 阶段 prompt
    )["prompt"]  # python 阶段 prompt 文本

    # 读取 checkpoint case 声明的结构字段与 prompt 可见性断言。
    dict_expectations = _case_expectations(case)  # 控制 checkpoint 结构与 prompt 暴露检查的开关集合

    # checkpoint 同时检查 planning 结构和 python prompt 暴露情况。
    dict_checks = _enabled_checks(  # checkpoint 的 plan 结构与 prompt 暴露检查
        {
            "has_structured_checkpoints": (  # semantic checkpoint 列表必须存在且每项都带 verification_hint
                bool(dict_plan.get("semantic_checkpoints"))  # semantic checkpoint 列表本身必须非空
                and all(  # 每个 checkpoint 记录里都必须带 verification_hint
                "verification_hint" in dict_item  # 单个 checkpoint 是否带验证提示
                for dict_item in dict_plan.get("semantic_checkpoints", [])  # 逐个遍历 semantic checkpoint 记录
                )
            ),  # 每个 semantic checkpoint 是否都带 verification_hint
            "has_structured_dependency_graph": (  # dependency graph 字段必须保留 nodes/edges 两类结构
                isinstance(dict_plan.get("subfunction_dependency_graph"), dict)  # dependency graph 主体是否仍保持结构化字典形态
                and "nodes" in dict_plan["subfunction_dependency_graph"]  # dependency graph 是否保留节点清单入口
                and "edges" in dict_plan["subfunction_dependency_graph"]  # dependency graph 是否保留边关系清单入口
            ),  # 依赖图是否保留节点与边的结构化表示
            "python_prompt_mentions_checkpoints": (  # python prompt 里必须同时点名 semantic checkpoint 与 verification hint
                "semantic_checkpoints" in str_python_prompt  # prompt 是否显式提到 semantic_checkpoints 字段
                and "verification_hint" in str_python_prompt  # prompt 是否明确暴露 checkpoint 级验证提示这一语义
            ),  # python prompt 是否显式暴露 checkpoint 语义
        },
        dict_expectations,  # 让 batch 汇总只评估用例明确要求的目录落盘与 prompt 结构断言
    )

    # checkpoint case 的 source 是 spec。
    return _report_from_checks(
        case,
        str_case_id,
        dict_checks,
        spec=str(case["spec"]),
        stable=True,
    )

# generation-mode case 验证 deep_review 额外 review 阶段。
def _evaluate_generation_mode_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 deep_review 模式是否保留 review 阶段证据。

    参数:
        case: generation-mode 回归配置，提供 spec 和期望开关。
        str_case_id: 当前 generation-mode 回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录 deep_review 模式下 review 阶段和 workflow 状态的报告。

    异常:
        无显式抛出的业务异常；底层 workflow 调用异常按原样向上传播。
    """

    # 该回归复用普通 spec，只切换 generation_mode 参数。
    path_spec = SKILL_ROOT / str(case["spec"])  # deep_review 模式读取的 spec

    # 读取 deep_review 回归要消费的规格内容。
    dict_spec = _read_json(path_spec)  # 传入 workflow 的 generation-mode 规格

    # generation-mode 目录里会保留 deep_review 独有的 review 证据。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # 保存 deep_review 专属工件的目录

    # mock provider 让 deep_review case 保持离线可复验。
    dict_result = run_verilog_workflow(  # deep_review 模式下的 workflow 执行结果
        dict_spec,  # deep_review 规格输入
        out_dir=path_case_root / "deep-review",  # review 阶段证据的专属输出目录
        provider_name="mock",  # 离线回归使用 mock provider
        generation_mode="deep_review",  # 显式启用 deep_review 模式
        run_external=False,  # 不触发外部工具链
    )  # deep_review workflow 结果

    # 最终 attempt 的 stage_outputs 应明确保留 review 阶段证据。
    dict_attempt = dict_result["workflow_result"]["attempts"][-1]  # 最后一轮 attempt，内部应保留 review stage_outputs

    # 读取 deep_review case 明确打开的 review 断言。
    dict_expectations = _case_expectations(case)  # 控制 deep_review 证据检查启停的开关集合

    # review_stage_present 用来证明这条链路真的走过 deep_review。
    dict_checks = _enabled_checks(  # deep_review 的 review 阶段与 workflow 状态检查
        {
            "review_stage_present": "review" in dict_attempt.get("stage_outputs", {}),  # review 阶段证据是否写进最终 attempt
            "workflow_passes": dict_result["status"] == "passed",  # deep_review 主流程是否整体通过
        },
        dict_expectations,  # 仅保留 case 显式打开的断言项
    )

    # 标准报告保留 spec 字段。
    return _report_from_checks(
        case,
        str_case_id,
        dict_checks,
        spec=str(case["spec"]),
    )

# streaming case 验证流式 transcript 和 stage 标记。
def _evaluate_streaming_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 streaming 模式是否留下 transcript 证据。

    参数:
        case: streaming 回归配置，提供 spec 和期望开关。
        str_case_id: 当前 streaming 回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录 stream 标记、transcript 文件和 workflow 结果的报告。

    异常:
        无显式抛出的业务异常；底层 workflow 调用和文件读取异常按原样向上传播。
    """

    # streaming 回归沿用普通 spec，只额外打开 stream 开关。
    path_spec = SKILL_ROOT / str(case["spec"])  # streaming 模式沿用的规格输入

    # 读入规格后直接送入流式 workflow，不再追加额外转换。
    dict_spec = _read_json(path_spec)  # 传给流式 workflow 的原始 spec 内容

    # 独立目录用于同时容纳 attempt 产物和 transcript 文本。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # streaming run 与 transcript 的隔离目录

    # 这里真正调用开启 stream 的 workflow，确认不是只打一个配置标记。
    dict_result = run_verilog_workflow(  # 启用 stream 的 workflow 执行结果
        dict_spec,  # streaming case 的规格输入
        out_dir=path_case_root / "streaming",  # 流式 workflow 的运行目录
        provider_name="mock",  # 统一锁定 mock provider 以便离线复验
        generation_mode="regular",  # 维持常规生成模式
        stream=True,  # 打开流式输出开关
        run_external=False,  # 流式回归只验证 transcript 行为，不调用外部工具链
    )  # 返回含 stream_used 和 attempt 目录的 workflow 结果

    # attempt 摘要和 transcript 文件要同时证明流式路径真正执行过。
    dict_attempt = dict_result["workflow_result"]["attempts"][-1]  # 最终 attempt，后面会用它回拼 transcript 路径

    # 抽取 RTL 阶段摘要，确认 stream 标记是否落盘。
    dict_rtl_stage = dict_attempt.get("stage_outputs", {}).get("rtl", {})  # RTL 阶段流式输出摘要

    # 根据最终 attempt_id 回拼 RTL transcript 的真实落盘路径。
    path_transcript = (  # 该次 streaming run 的 transcript 路径
        path_case_root  # 当前 case 的工件根
        / "streaming"  # workflow 运行目录
        / dict_attempt["attempt_id"]  # 最终 attempt 子目录
        / "rtl"  # RTL 阶段子目录
        / "rtl_stream.txt"  # 流式 transcript 文本文件
    )  # 最终定位到实际 transcript 文件

    # 读取 streaming case 声明的标记、transcript 和状态断言。
    dict_expectations = _case_expectations(case)  # 控制 stream 标记与 transcript 检查的开关集合

    # 这些检查一起防止“只记了 stream_used，但没有真实 transcript”。
    dict_checks = _enabled_checks(  # 流式标记、transcript 和主流程状态检查
        {
            "stream_used": bool(dict_rtl_stage.get("stream_used")),  # RTL 阶段是否记录 stream_used 标记
            "stream_transcript_present": path_transcript.is_file(),  # transcript 文件是否已落盘
            "workflow_passes": dict_result["status"] == "passed",  # 流式路径执行完后主 workflow 是否仍保持 passed
        },
        dict_expectations,  # 仅保留 case 显式启用的断言项
    )

    # streaming 报告继续回传 spec，方便把 transcript 证据对应回原始规格。
    return _report_from_checks(
        case,
        str_case_id,
        dict_checks,
        spec=str(case["spec"]),
    )

# batch case 验证多 spec 隔离生成。
def _evaluate_batch_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 batch generation 是否逐 case 生成并汇总通过。

    参数:
        case: batch 回归配置，提供 `specs` 列表和启用的检查项。
        str_case_id: 当前 batch 回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录 batch 数量、逐 case 状态和工件目录完整性的报告。

    异常:
        无显式抛出的业务异常；底层 spec 读取和 batch workflow 异常按原样向上传播。
    """

    # specs 列表会按顺序展开成 batch workflow 的输入载荷。
    list_specs: list[dict[str, Any]] = []  # batch 输入 spec 集合

    # 逐个读取 spec，并为重复模块名添加序号后缀。
    for int_index, str_spec_ref in enumerate(case.get("specs", []), start=1):

        # 每个 spec 都从 skill 根读取，保持 evals.json 可移植。
        dict_spec = _read_json(SKILL_ROOT / str(str_spec_ref))  # batch 单个 spec 内容

        # 第二个及之后的 spec 会追加序号，避免输出路径互相覆盖。
        if int_index > 1:

            # 模块名加后缀后，能够直接从输出文件名看出 batch 序号。
            dict_spec["name"] = f"{dict_spec['name']}_{int_index}"  # batch 内的唯一模块名

            # outputs 列表也要同步改名，才能验证 batch 输出隔离。
            dict_spec["outputs"] = [
                {"path": f"rtl/{dict_spec['name']}.v", "kind": "source", "language": "verilog"},  # 主 RTL 输出路径
                {"path": f"tb/{dict_spec['name']}_tb.v", "kind": "testbench", "language": "verilog"},  # 对应 testbench 输出路径
            ]  # 当前 batch spec 的输出契约

        # 加入 batch 输入列表，保持原顺序。
        list_specs.append(dict_spec)

    # batch 目录里会同时保留 summary 和每个子 case 的 run 结果。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # 容纳 batch summary 与子 case 工件的目录

    # 这里验证的是批量编排逻辑，不混入外部工具链或 deep_review 变量。
    dict_result = run_verilog_batch(  # batch 规格集合的统一执行结果
        list_specs,  # 按顺序展开后的 batch 规格集合
        out_dir=path_case_root / "batch",  # batch 汇总工件目录
        provider_name="mock",  # 批量回归统一锁定 mock provider
        generation_mode="regular",  # 不把批量行为与 deep_review 混在一起
        run_external=False,  # 只验证批量编排逻辑
    )  # batch workflow 汇总结果

    # batch case 会按 expectations 开关裁剪最终断言集合。
    dict_expectations = _case_expectations(case)  # 控制 batch 汇总与逐 case 检查启停的开关集合

    # 这组检查覆盖 summary 数量、逐 case 状态和 artifact 目录完整性。
    dict_checks = _enabled_checks(  # batch case 的数量、状态与工件完整性检查
        {
            "case_count_matches": (  # 汇总数量必须与展开后的输入数一致
                dict_result["summary"]["case_count"] == len(list_specs)  # 汇总中的 case 数量
            ),  # summary 中的 case 数量是否与输入一致
            "all_cases_passed": all(  # 每个 batch 子 case 都必须保持 passed
                dict_item.get("status") == "passed"  # 单个 batch case 的状态字段
                for dict_item in dict_result.get("cases", [])  # 逐个遍历 batch 汇总里的 case 条目
            ),  # 每个 batch case 是否都通过
            "artifact_dirs_present": all(  # 只要 case 声明了 artifact_dir，该目录就必须真实落盘
                Path(dict_item["artifact_dir"]).exists()  # 当前 batch case 的 artifact_dir 是否存在
                for dict_item in dict_result.get("cases", [])  # 遍历 batch 汇总里每个已执行 workflow case 的结果记录
                if dict_item.get("artifact_dir")  # 只对显式声明了 artifact_dir 的 case 做目录存在性检查
            ),  # 每个 case 的 artifact_dir 是否都真实存在
        },
        dict_expectations,  # 只启用 batch case 为 artifact_dir 检查和 prompt 字段检查显式打开的项目
    )

    # 标准报告不需要额外 source 字段。
    return _report_from_checks(case, str_case_id, dict_checks)

# merge assist case 验证计划型合并辅助产物。
def _evaluate_merge_assist_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 merge_assist 是否产出计划、wrapper 和验证摘要。

    参数:
        case: merge_assist 回归配置，提供输入 RTL 和期望开关。
        str_case_id: 当前 merge_assist 回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录 merge plan、merge wrapper、merge validation 和 planned 状态的报告。

    异常:
        无显式抛出的业务异常；底层 refine 调用和 merge 摘要读取异常按原样向上传播。
    """

    # merge_assist 回归读取的是 existing RTL 输入夹具。
    path_source = SKILL_ROOT / str(case["source"])  # merge_assist 规划要消费的 existing RTL

    # merge_assist 目录要同时容纳 plan、wrapper 和 validation 摘要。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # merge_assist 规划工件的隔离目录

    # merge_assist 默认停在计划态，只生成供人工审阅的辅助工件。
    dict_result = improve_existing_verilog(  # merge_assist 的 planned 工件总表，供 merge_plan_present 等四个 merge_* 断言统一取路径
        path_source,  # merge_assist 要审阅的 existing RTL 输入源文件
        out_dir=path_case_root / "merge",  # merge_assist 计划包的输出目录
        improve_goal="merge_assist",  # 仅生成合并辅助工件
    )  # merge_assist 执行结果

    # 读取 merge_assist case 对计划态产物的断言集合。
    dict_expectations = _case_expectations(case)  # 控制 merge 计划态工件检查启停的开关集合

    # merge_equivalence JSON 用来确认当前阶段仍停留在 planned。
    dict_merge_equivalence = _read_json(  # merge_assist 的等价性摘要字典，merge_equivalence_planned 断言会直接读取它的 status 字段
        Path(dict_result["artifacts"]["merge_equivalence"])  # 指向 merge_assist 计划态等价性摘要 JSON 的工件路径
    )  # merge equivalence 的计划态摘要

    # 这里同时核对 merge 计划工件和 planned 状态摘要。
    dict_checks = _enabled_checks(  # merge_assist 工件与 planned 状态检查
        {
            "merge_plan_present": Path(dict_result["artifacts"]["merge_plan"]).is_file(),  # merge 计划说明文件是否写出
            "merge_wrapper_present": Path(dict_result["artifacts"]["merge_wrapper"]).is_file(),  # 供人工比对的 wrapper 文件是否写出
            "merge_validation_present": Path(dict_result["artifacts"]["merge_validation"]).is_file(),  # merge 验证摘要文件是否写出
            "merge_equivalence_planned": (  # merge_equivalence 仍应停留在计划态
                dict_merge_equivalence.get("status") == "planned"  # 当前阶段是否仍停留在 planned
            ),  # merge equivalence 是否仍保持 planned
        },
        dict_expectations,  # 只启用 merge_assist case 为计划态工件显式打开的检查项
    )

    # merge_assist 报告仍返回 source，方便把计划工件回指到输入 RTL。
    return _report_from_checks(
        case,
        str_case_id,
        dict_checks,
        source=str(case["source"]),
        stable=True,
    )

# optimize assist case 验证无候选和有候选两种路径。
def _evaluate_optimize_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 optimize_assist 是否保持 advisory 行为。

    参数:
        case: optimize_assist 回归配置，提供输入 RTL 和期望开关。
        str_case_id: 当前 optimize_assist 回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录无候选路径、有候选路径和 QoR 摘要状态的报告。

    异常:
        无显式抛出的业务异常；底层 refine 调用和 validation 读取异常按原样向上传播。
    """

    # optimize_assist 回归读取待优化 RTL，后续会分别走无候选和有候选两条路径。
    path_source = SKILL_ROOT / str(case["source"])  # optimize_assist 要评估的基准 RTL

    # optimize_assist 目录需要并排保存 plan-only 与 candidate 两组输出。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # optimize_assist 双路径的隔离目录

    # 无候选时应只给计划和 QoR 摘要，不自动改写。
    dict_without_candidate = improve_existing_verilog(  # 无候选输入时的 optimize 执行结果
        path_source,  # 无候选路径的原始 RTL 输入
        out_dir=path_case_root / "opt",  # 无候选路径输出目录
        improve_goal="optimize_assist",  # 强制走 optimize_assist 的候选对照分支
    )  # 无候选 optimize assist 结果

    # 候选目录单独隔离，便于把有候选路径与 plan-only 路径分开。
    path_candidate_dir = path_case_root / "candidate_dir"  # optimize 候选 RTL 的暂存目录

    # 复制一份候选 RTL，供带候选路径做对照验证。
    path_candidate = _copy_fixture(  # 送入 optimize_assist 的候选 RTL 副本
        path_source,  # 原始 RTL 作为候选副本源
        path_candidate_dir,  # 候选目录根
        "candidate.v",  # 候选 RTL 文件名
    )

    # 带候选路径应额外给出 equivalence 与 QoR 报告，证明进入对照分析分支。
    dict_with_candidate = improve_existing_verilog(  # 带候选输入时的 optimize 执行结果
        path_source,  # 与无候选路径复用同一 RTL 输入
        out_dir=path_case_root / "opt-with-candidate",  # 有候选路径输出目录
        improve_goal="optimize_assist",  # optimize 辅助模式
        candidate_artifacts_dir=path_candidate.parent,  # 候选 RTL 所在目录
    )  # 有候选 optimize assist 结果

    # 两份 validation JSON 共同描述 advisory 模式的推荐动作与 QoR 摘要。
    dict_without_validation = _read_json(  # 无候选路径 validation JSON 的解析结果
        Path(dict_without_candidate["transform_validation_path"])  # 无候选路径 validation JSON
    )  # 无候选路径的 validation 结果

    # 读取带候选路径的 validation JSON。
    dict_with_validation = _read_json(  # 带候选路径 validation JSON 的解析结果
        Path(dict_with_candidate["transform_validation_path"])  # 有候选路径 validation JSON
    )  # 有候选路径的 validation 结果

    # 提取 optimize 回归显式打开的断言项。
    dict_expectations = _case_expectations(case)  # optimize case 需要执行的断言集合

    # 汇总 optimize 两条路径各自需要满足的检查结果。
    dict_checks = _enabled_checks(  # optimize 两条路径的最终检查集合
        {
            "plan_only_without_candidate": (  # 无候选路径仍应停留在 plan-only 建议
                dict_without_validation.get("recommended_next_action")  # 无候选路径给出的下一步动作
                == "provide_candidate_rtl_or_review_plan"  # 无候选时继续要求人工提供候选
            ),  # 无候选路径是否仍保持 plan-only 建议
            "qor_summary_present": (  # 两条 optimize 路径都必须保留 QoR 摘要
                "qor_summary" in dict_with_validation  # 有候选路径的 QoR 摘要字段
                and "qor_summary" in dict_without_validation  # 无候选路径的 QoR 摘要字段
            ),  # 两条路径是否都保留 QoR 摘要
            "candidate_compare_outputs_present": (  # 候选路径必须补齐 equivalence 与 qor_report 两类对照工件
                "equivalence" in dict_with_candidate.get("artifacts", {})  # 有候选路径是否写出 equivalence 工件
                and "qor_report" in dict_with_candidate.get("artifacts", {})  # 有候选路径是否额外落盘供人工 QoR 对照的 qor_report
            ),  # 有候选路径是否生成比较与 QoR 工件
        },
        dict_expectations,  # 只启用 optimize case 为 compare/QoR 结果显式打开的检查项
    )

    # 返回报告时继续暴露 source 字段，方便定位输入夹具。
    return _report_from_checks(
        case,
        str_case_id,
        dict_checks,
        source=str(case["source"]),
        stable=True,
    )

# verify diagnostics case 检查诊断包完整性。
def _evaluate_verify_existing_diagnostics_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 verify-existing 保守诊断模式的工件包。

    参数:
        case: diagnostics 回归配置，提供输入 RTL 和可选规格说明。
        str_case_id: 当前 diagnostics 回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录各类诊断 JSON/Markdown 工件是否齐全的报告。

    异常:
        无显式抛出的业务异常；底层 verify 调用异常按原样向上传播。
    """

    # diagnostics 回归读取的是需要做保守诊断的 RTL 输入。
    path_source = SKILL_ROOT / str(case["source"])  # verify diagnostics 要分析的基准 RTL

    # 解析 diagnostics 可选使用的规格说明输入。
    path_spec_source = _optional_skill_path(case, "spec_source")  # 辅助诊断的可选规格说明

    # diagnostics 输出目录要容纳多份 JSON/Markdown 诊断包。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # diagnostics 工件包的隔离目录

    # conservative 模式只生成诊断包，不进入任何自动改写流程。
    dict_result = verify_existing_verilog(  # diagnostics 工件路径总表，simulation_slice_present 到 terminal_status_present 八项断言都从这里取值
        path_source,  # 本轮 diagnostics 要审阅的 RTL 输入
        out_dir=path_case_root / "verify-diagnostics",  # verify diagnostics 工件包目录
        spec_source=path_spec_source,  # 仅用于补充诊断上下文的规格说明
        automation_mode="conservative",  # 维持只出报告不改写的模式
        tb_mode="generate",  # diagnostics 默认沿用 generate 型测试平台生成路径
        tb_language="verilog",  # diagnostics 链路固定使用 Verilog 版测试平台语言
        readiness="static",  # 只做静态准备度诊断
        run_external=False,  # 不触发外部仿真工具链
    )  # 返回 verify-existing diagnostics 的整套工件位置

    # 读取 diagnostics case 明确声明的检查开关。
    dict_expectations = _case_expectations(case)  # diagnostics case 的断言集合

    # 每个诊断字段都应落成单独工件，方便 host 精确定位缺口。
    dict_checks = _enabled_checks(  # diagnostics 工件完整性的检查集合
        {
            "simulation_slice_present": Path(dict_result["simulation_slice_path"]).is_file(),  # 仿真切片工件是否写出
            "timing_diagnostic_present": Path(dict_result["timing_diagnostic_path"]).is_file(),  # 时序诊断工件是否写出
            "expected_trace_present": Path(dict_result["expected_trace_path"]).is_file(),  # 期望波形轨迹工件是否写出
            "waveform_diff_present": Path(dict_result["waveform_diff_path"]).is_file(),  # 波形差异工件是否写出
            "testcase_matrix_present": Path(dict_result["testcase_matrix_path"]).is_file(),  # testcase 矩阵工件是否写出
            "run_summary_present": Path(dict_result["run_summary_path"]).is_file(),  # 运行摘要工件是否写出
            "synth_readiness_present": Path(dict_result["synth_readiness_path"]).is_file(),  # 综合准备度工件是否写出
            "terminal_status_present": Path(dict_result["terminal_status_path"]).is_file(),  # 终端状态工件是否写出
        },
        dict_expectations,  # 只启用 diagnostics case 为诊断包完整性显式打开的检查项
    )

    # 报告中继续保留 source 字段，便于回指原始 RTL。
    return _report_from_checks(
        case,
        str_case_id,
        dict_checks,
        source=str(case["source"]),
        stable=True,
    )

# verify augment case 检查 testbench 增强和 auto_apply 行为。
def _evaluate_verify_existing_augment_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 verify-existing augment testbench 的保守与自动应用路径。

    参数:
        case: augment 回归配置，提供 RTL、规格说明和 legacy testbench。
        str_case_id: 当前 augment 回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录保守路径、自动应用路径和 testbench 注入结果的报告。

    异常:
        无显式抛出的业务异常；底层 verify 调用和合同文件读取异常按原样向上传播。
    """

    # augment 回归先锁定要增强 testbench 的那份 RTL。
    path_source = SKILL_ROOT / str(case["source"])  # augment 流程里要挂接 testbench 的基准 RTL

    # 解析 augment 路径依赖的规格说明夹具。
    path_spec_source = SKILL_ROOT / str(case["spec_source"])  # augment 规格说明夹具

    # legacy testbench 会被保守路径审阅，也会被 auto_apply 路径复制后改写。
    path_testbench_source = SKILL_ROOT / str(case["testbench_source"])  # 待增强的 legacy testbench 夹具

    # augment 需要并排保存 conservative 与 auto_apply 两套证据。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # augment 双路径的隔离工件目录

    # conservative 路径只生成 augment 计划和 diff。
    dict_conservative = verify_existing_verilog(  # augment 保守路径返回的计划与 diff 结果
        path_source,  # 需要补 testbench 的基准 RTL
        out_dir=path_case_root / "conservative",  # conservative 路径输出目录
        spec_source=path_spec_source,  # augment 规格说明输入
        testbench_source=path_testbench_source,  # 直接传入原始 testbench
        automation_mode="conservative",  # 仅生成计划与 diff
        tb_mode="augment",  # 走 testbench 增强模式
        tb_language="verilog",  # conservative 路径保留 Verilog
        readiness="static",  # 保守路径只验证静态增强结果
        run_external=False,  # 本轮不接仿真器，只看 augment 计划和 contract
    )  # 返回 conservative augment 分支的工件位置

    # auto_apply 需要复制 testbench，避免覆盖 skill example。
    path_auto_apply_tb = _copy_fixture(  # auto_apply 路径要改写的 testbench 副本
        path_testbench_source,  # 复制原始 legacy testbench 作为可改写副本源
        path_case_root / "auto_apply",  # auto_apply testbench 副本的落盘目录
    )

    # auto_apply 路径验证 Verilog testbench 自动注入和备份记录。
    dict_auto_apply = verify_existing_verilog(  # 自动注入 hooks 后的 augment 分支结果
        [path_source, path_auto_apply_tb],  # RTL 与可改写 testbench 的组合输入
        out_dir=path_case_root / "auto_apply_run",  # auto_apply 分支自己的 run 目录
        spec_source=path_spec_source,  # 与 conservative 共享同一份规格说明
        automation_mode="auto_apply",  # 允许自动应用 testbench 变更
        tb_mode="augment",  # 继续走 testbench 增强模式
        tb_language="verilog",  # Verilog-only 边界下保持 testbench 语言不升级
        readiness="static",  # 先只验证静态注入结果
        run_external=False,  # 本轮不接仿真器，只看注入后的 contract
    )  # 返回 auto_apply augment 分支写出的 contract、diff 与 verification 工件

    # 读取 conservative verification_result，后面要核对是否仍停在计划态。
    dict_conservative_result = _read_json(  # augment conservative 分支的 verification_result 字典，后面用它证明 tb_mutation.applied 仍为 False
        Path(dict_conservative["verification_result_path"])  # 指向 conservative 分支 verification_result JSON 证据文件的路径
    )  # 保守模式验证结果

    # conservative tb_contract 会暴露原始 testbench 来源和增强计划。
    dict_conservative_contract = _read_json(  # 保守分支 testbench 合同的原始 JSON 记录
        Path(dict_conservative["tb_contract_path"])  # 指向 conservative 分支写出的 tb_contract 文件
    )  # 保守模式测试平台契约

    # auto_apply verification_result 用来确认自动注入后的状态与策略。
    dict_auto_apply_result = _read_json(  # auto_apply 分支 verification_result 的原始 JSON 记录
        Path(dict_auto_apply["verification_result_path"])  # auto_apply 分支 verification_result 文件路径
    )  # 自动应用验证结果

    # auto_apply tb_contract 记录最终生效 testbench 的路径和语言。
    dict_auto_apply_contract = _read_json(  # auto_apply 分支激活 testbench 元数据对应的合同记录
        Path(dict_auto_apply["tb_contract_path"])  # 指向 auto_apply 最终激活 testbench 所属的 tb_contract 文件
    )  # 自动应用路径的测试平台契约

    # 读取自动应用后的激活 testbench 正文。
    str_active_tb = Path(  # 自动应用后实际生效的 testbench 文本
        dict_auto_apply_contract["active_testbench_path"]  # auto_apply 最终生效的 testbench 路径
    ).read_text(encoding="utf-8")  # auto_apply 后的 testbench 文本

    # 提取 augment case 最终启用的合同检查开关。
    dict_expectations = _case_expectations(case)  # 控制 augment 双路径合同检查启停的开关集合

    # 这里把保守路径与 auto_apply 路径的关键契约一起核对。
    dict_checks = _enabled_checks(  # augment 双路径的一致性检查集合
        {
            "explicit_tb_source_supported": (  # 原始 testbench 来源必须在 contract 中原样保留
                dict_conservative_contract.get("original_testbench_path")  # contract 里记录的原始 testbench 路径
                == str(path_testbench_source)  # 应当精确等于传入的 legacy testbench 路径
            ),  # 保守路径是否保留原始 testbench 来源
            "augment_plan_present": Path(dict_conservative["tb_augment_plan_path"]).exists(),  # augment 计划文件是否写出
            "diff_present": Path(dict_conservative["tb_augment_diff_path"]).exists(),  # augment 差异文件是否写出
            "tb_hooks_injected": all(  # 激活 testbench 必须补齐固定 hook 标记，证明增强内容真正写入
                str_tag in str_active_tb  # 当前 hook 标签是否已注入到激活 testbench
                for str_tag in (  # 逐个核对增强后的 testbench 必须包含的固定标签
                    "[TB_MONITOR]",  # 监控信息标签
                    "[TB_DATA]",  # 数据采样标签
                    "[TB_ERROR]",  # 错误报告标签
                    "[TB_INFO]",  # 普通信息标签
                    "VERILOG-GEN-RESULT",  # 生成器结果摘要标签
                )
            ),  # auto_apply 后的 testbench 是否注入监控 hook
            "auto_apply_backup_created": bool(  # auto_apply 分支是否在合同里登记了备份 testbench 路径
                dict_auto_apply_contract.get("backup_testbench_path")  # contract 中是否记录 backup_testbench_path
            )
            and Path(dict_auto_apply_contract["backup_testbench_path"]).exists(),  # auto_apply 路径是否留下 testbench 备份
            "auto_apply_verilog_recorded": (  # auto_apply augment 必须同时记录 Verilog 边界和自动注入策略
                dict_auto_apply_contract.get("language_after") == "verilog"  # 合同里是否声明增强后仍保持 Verilog
                and dict_auto_apply_result.get("tb_mutation", {}).get("policy") == "auto_apply"  # verification_result 是否记录自动应用策略
            ),  # 合同与验证结果是否记录 Verilog-only 自动应用
        },
        dict_expectations,  # 只启用 augment case 为双路径 contract 与自动应用显式打开的检查项
    )

    # 只有 conservative 未应用且 auto_apply 已应用时，双路径状态才算稳定。
    bool_stable = (
        not dict_conservative_result.get("tb_mutation", {}).get("applied")  # conservative 路径保持只计划不落盘
        and dict_auto_apply_result.get("tb_mutation", {}).get("applied")  # auto_apply 路径真实写入变更
    )  # augment 双路径稳定性

    # 标准报告保留 source 字段和自定义 stable。
    return _report_from_checks(
        case,
        str_case_id,
        dict_checks,
        source=str(case["source"]),
        stable=bool_stable,
    )

# verify RTL repair case 检查 conservative resume、auto_apply 和阻断路径。
def _evaluate_verify_existing_rtl_repair_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 verify-existing RTL patch 的确认恢复和自动应用边界。

    参数:
        case: RTL repair 回归配置，提供源 RTL、规格说明和阻断场景输入。
        str_case_id: 当前 RTL repair 回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录确认恢复、自动应用和多文件阻断路径状态的报告。

    异常:
        无显式抛出的业务异常；底层 verify 调用和 JSON 读取异常按原样向上传播。
    """

    # repair 回归从原始 RTL 出发，后面会复制出三条验证路径。
    path_source = SKILL_ROOT / str(case["source"])  # repair 流程的基准 RTL 输入

    # 解析 RTL repair 需要参照的规格说明。
    path_spec_source = SKILL_ROOT / str(case["spec_source"])  # RTL repair 规格说明

    # repair 三条路径共享同一工件根，便于对照确认恢复、自动应用和阻断。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # repair 三路径共用的工件根

    # 复制 RTL 到本地工件目录，避免改动受版本控制的夹具。
    path_local_source = _copy_fixture(path_source, path_case_root)  # conservative 本地 RTL 副本

    # conservative 首跑生成 patch_candidate 和 intervention。
    verify_existing_verilog(
        path_local_source,
        out_dir=path_case_root / "conservative",
        spec_source=path_spec_source,
        automation_mode="conservative",
        tb_mode="generate",
        tb_language="verilog",
        readiness="static",
        run_external=False,
    )

    # decision.json 模拟用户确认应用低风险 reset patch。
    path_decision = _write_decision(  # conservative resume 决策文件的写出结果
        path_case_root / "decision.json",  # conservative resume 的决策文件路径
        evidence="confirm low-risk reset patch",  # 模拟用户确认低风险 reset patch
    )  # conservative resume 决策文件

    # resume 会复用同一 run 目录，验证用户确认后能继续应用 patch。
    dict_resumed = verify_existing_verilog(  # 恢复确认后的 conservative repair 结果
        path_local_source,  # resume 后继续使用同一份本地 RTL 副本
        out_dir=path_case_root / "conservative",  # 复用 conservative run 目录
        spec_source=path_spec_source,  # 与首轮 conservative 共享同一规格说明
        automation_mode="conservative",  # 仍按确认式应用流程执行
        tb_mode="generate",  # repair 路径使用 generate 型 testbench
        tb_language="verilog",  # repair 路径使用 Verilog testbench
        decision_source=path_decision,  # 注入用户确认决策
        readiness="static",  # 恢复后仍只做静态验证
        run_external=False,  # 这条恢复链路不接外部工具，只看 patch 应用
    )  # 返回确认恢复后的 repair 工件位置

    # auto_apply 使用单独副本，验证低风险类别可自动应用。
    path_auto_source = _copy_fixture(  # auto_apply repair 路径使用的 RTL 副本
        path_source,  # 从原始 RTL 再复制一份给 auto_apply 分支
        path_case_root,  # 为 auto_apply repair 副本单独预留的工作根
        "auto_apply_source.v",  # auto_apply repair 副本的稳定文件名
    )

    # auto_apply 路径专门验证低风险补丁是否可以直接落地。
    dict_auto_apply = verify_existing_verilog(  # auto_apply repair 的结果总表，后面要从 verification_result_path 与 run_dir 证明补丁已落地
        path_auto_source,  # 单独副本用于 auto_apply 路径
        out_dir=path_case_root / "auto-apply",  # auto_apply repair 分支的输出目录
        spec_source=path_spec_source,  # 与其他 repair 分支共用同一规格说明
        automation_mode="auto_apply",  # 允许自动应用低风险补丁
        tb_mode="generate",  # auto_apply repair 继续沿用 generate 型测试平台生成路径
        tb_language="verilog",  # auto_apply repair 仍保持 Verilog 测试平台语言配置
        readiness="static",  # 这条链路只检查静态应用结果
        run_external=False,  # 本轮不跑外部工具链，只核对 patch 行为
    )  # 返回 auto_apply repair 分支写出的补丁、验证与阻断说明工件

    # blocked_sources 用来模拟多文件输入，验证 auto_apply 会被强制降级。
    list_blocked_sources = _copy_blocked_sources(case, path_case_root)  # 多文件阻断输入集合

    # blocked 路径应写出 intervention，而不是继续应用 RTL patch。
    dict_blocked = verify_existing_verilog(  # 多文件 blocked 路径的执行结果
        list_blocked_sources,  # 多文件阻断场景的输入集合
        out_dir=path_case_root / "blocked",  # blocked 分支输出目录
        automation_mode="auto_apply",  # 验证 auto_apply 是否被安全降级
        tb_mode="generate",  # blocked 分支使用 generate 型 testbench
        tb_language="verilog",  # blocked 分支使用 Verilog testbench
        readiness="static",  # blocked 分支只保留静态阻断证据
        run_external=False,  # 多文件阻断场景不接外部工具链
    )  # 多文件阻断结果

    # 读取关键 JSON 产物用于检查 patch 状态。
    # resume verification_result 记录确认后是否应用 RTL patch。
    dict_resumed_payload = _read_json(Path(dict_resumed["verification_result_path"]))  # 确认恢复后的验证结果

    # resume patch_candidate 记录备份路径和应用约束。
    dict_resumed_patch = _read_json(Path(dict_resumed["patch_candidate_path"]))  # 确认恢复后的补丁候选

    # auto_apply verification_result 记录低风险补丁自动应用状态。
    dict_auto_payload = _read_json(Path(dict_auto_apply["verification_result_path"]))  # 自动应用分支的验证结果

    # blocked verification_result 记录多文件输入是否被阻断。
    dict_blocked_payload = _read_json(Path(dict_blocked["verification_result_path"]))  # 多文件阻断分支的验证结果

    # blocked patch_candidate 会暴露 apply_blockers，证明阻断原因已被记录。
    dict_blocked_patch = _read_json(Path(dict_blocked["patch_candidate_path"]))  # 多文件阻断分支的阻断补丁候选

    # 提取 repair case 显式启用的断言集合。
    dict_expectations = _case_expectations(case)  # RTL repair case 的期望集合

    # 汇总 repair 三条路径要满足的最终检查结果。
    dict_checks = _enabled_checks(  # repair 三条路径的综合检查集合
        {
            "conservative_resume_apply": bool(dict_resumed_payload.get("rtl_mutation", {}).get("applied")),  # conservative resume 是否完成补丁应用
            "auto_apply_low_risk": (  # 低风险补丁在 auto_apply 下应直接落地
                dict_auto_payload.get("rtl_mutation", {}).get("policy") == "auto_apply"  # 低风险路径的 mutation 策略
                and bool(dict_auto_payload.get("rtl_mutation", {}).get("applied"))  # 低风险 patch 是否真的被应用
            ),  # 低风险补丁是否自动应用
            "backup_created": bool(dict_resumed_patch.get("backup_rtl_paths"))  # confirm-resume 恢复链路是否登记原 RTL 备份
            and bool(dict_auto_payload.get("rtl_mutation", {}).get("backup_rtl_paths")),  # auto_apply 直写链路是否也登记备份 RTL 路径
            "post_apply_validation_present": (  # confirm-resume 与 auto_apply 两条路径都应补写 apply 后验证结果
                Path(dict_resumed["run_dir"], "post_apply_validation.json").exists()  # confirm-resume 路径是否写出 post_apply_validation
                and Path(dict_auto_apply["run_dir"], "post_apply_validation.json").exists()  # auto_apply 路径是否也写出 post_apply_validation
            ),  # 应用后静态验证产物是否存在
            "blocked_multi_file_intervention": (  # 多文件输入必须记录 blocker，并切到 intervention 人工处理路径
                "multiple_source_files" in dict_blocked_patch.get("apply_blockers", [])  # patch_candidate 是否登记多文件阻断原因
                and Path(dict_blocked["run_dir"], "rtl_intervention.json").exists()  # blocked 运行目录里是否落盘人工介入说明
                and not bool(dict_blocked_payload.get("rtl_mutation", {}).get("applied"))  # 阻断后是否确实没有继续自动应用 RTL 补丁
            ),  # 多文件输入是否进入人工介入路径
        },
        dict_expectations,  # 只启用 repair case 为补丁应用与阻断证据显式打开的检查项
    )

    # repair 报告继续暴露 source，方便把补丁证据回指到原始 RTL。
    return _report_from_checks(
        case,
        str_case_id,
        dict_checks,
        source=str(case["source"]),
    )

# patch-library case 检查新增控制/时序补丁类别的确认边界。
def _evaluate_verify_existing_rtl_patch_library_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 RTL patch library 中控制和时序类别的确认式应用。

    参数:
        case: patch library 回归配置，提供 control/timing 两组源文件与规格说明。
        str_case_id: 当前 patch library 回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录 control 类别、timing 类别和确认后回归状态的报告。

    异常:
        无显式抛出的业务异常；底层 patch-library 分支运行异常按原样向上传播。
    """

    # patch library 让 control/timing 两条分支共享一套工件根，便于并排对照。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # patch library 双分支共用工件目录

    # 提取 patch-library case 明确启用的断言集合。
    dict_expectations = _case_expectations(case)  # 控制 patch-library 双分支检查启停的开关集合

    # control 场景首跑应降级到确认。
    dict_control = _run_patch_library_branch(  # control 分类分支的完整运行证据
        source=SKILL_ROOT / str(case["control_source"]),  # control 分类使用的源 RTL
        spec_source=SKILL_ROOT / str(case["control_spec_source"]),  # control 分类对应的规格说明
        case_root=path_case_root,  # control/timing 共用的 patch-library 工件根
        branch_name="control",  # 显式固定为 control patch 分类
        evidence="confirm control logic patch",  # control 分类恢复运行时注入的确认凭据
    )  # control patch 分支证据

    # timing 场景会走寄存器补丁分类，同样应该先停在确认态。
    dict_timing = _run_patch_library_branch(  # timing 时序补丁分类分支的运行证据
        source=SKILL_ROOT / str(case["timing_source"]),  # timing 分类要修补寄存器缺口的源 RTL
        spec_source=SKILL_ROOT / str(case["timing_spec_source"]),  # 与 timing RTL 配套的寄存器时序规格说明
        case_root=path_case_root,  # 复用 patch-library 共用工件根
        branch_name="timing",  # 显式固定为寄存器补全分支，避免误落到 control 默认补全分类
        evidence="confirm timing register patch",  # timing 分支 resume 时回放的人工确认短语
    )  # 返回 timing patch 分类分支的运行证据

    # 检查补丁类别、降级策略、intervention 和确认后回归产物。
    dict_checks = _enabled_checks(  # patch-library 双分支的综合检查集合
        {
            "control_patch_category_detected": dict_control["first_plan"].get("patch_category")  # control 分支应识别为默认补全过程
            == "case_default_completion",  # control 分支是否识别到控制逻辑补丁类别
            "timing_patch_category_detected": dict_timing["first_plan"].get("patch_category")  # timing 分支应识别为输出寄存器补全过程
            == "output_register_completion",  # timing 分支是否识别到寄存器补丁类别
            "auto_apply_downgraded_to_confirmation": (  # control/timing 首轮都应先落成 intervention，再等待显式确认
                _confirm_before_apply(dict_control["first_payload"])  # control 分支首轮是否先停在确认态
                and _confirm_before_apply(dict_timing["first_payload"])  # timing 首轮 payload 是否同样先被路由到待人工确认状态
            ),  # 两条分支是否都从 auto_apply 降级为确认式应用
            "intervention_present_before_apply": (  # 两个首轮 run_dir 都必须写出 intervention 说明文件
                Path(dict_control["first_run_dir"], "rtl_intervention.json").exists()  # control 分支是否写出 intervention 文件
                and Path(dict_timing["first_run_dir"], "rtl_intervention.json").exists()  # timing 首轮 run_dir 里是否也落盘人工介入说明
            ),  # 首轮运行是否都写出 intervention 说明
            "decision_resume_applies_and_regresses": (  # control/timing 两条 confirm-resume 链路都必须完成应用并通过回归
                _resume_applied_with_regression(dict_control)  # control 分支确认恢复后是否完成应用并带回归证据
                and _resume_applied_with_regression(dict_timing)  # timing 分支确认恢复后是否同时补写 patch 结果与回归记录
            ),  # 确认恢复后是否都完成应用与回归
        },
        dict_expectations,  # 只启用 recovery case 为 confirm-resume 决策链显式打开的检查项
    )

    # 标准报告的 source 使用 control_source 保持旧字段。
    return _report_from_checks(
        case,
        str_case_id,
        dict_checks,
        source=str(case["control_source"]),
    )

# routing case 检查只读入口决策和 provenance 策略。
def _evaluate_routing_case(
    case: dict[str, Any],
    str_case_id: str,
    temp_root: Path,
) -> dict[str, Any]:
    """评估 route_verilog_request 的入口推荐、缺失输入和风险标记。

    参数:
        case: routing 回归配置，提供 request summary、可选输入和期望决策字段。
        str_case_id: 当前 routing 回归的稳定标识。
        temp_root: 当前整轮评估共享的临时根目录。

    返回:
        记录推荐入口、缺失输入、风险标记和 provenance 策略的报告。

    异常:
        无显式抛出的业务异常；底层 routing 决策异常按原样向上传播。
    """

    # routing 回归会在临时目录里拼装多种只读入口场景。
    path_case_root = _prepare_case_root(temp_root, str_case_id)  # routing 决策使用的工件目录

    # rtl 输入既可能是真实夹具，也可能是刻意缺失的占位路径。
    path_source = _routing_source_path(case, path_case_root)  # route 输入 RTL 或缺失占位

    # spec 输入为 route 决策提供可选规格上下文。
    path_spec_source = _optional_skill_path(case, "spec_source")  # route 输入规格说明

    # artifact_dir 用于模拟已有 workflow 产物目录。
    path_artifact_dir = _routing_artifact_dir(case, path_case_root)  # route 产物目录输入

    # codegen_plan 会模拟上游已经存在的规划上下文。
    dict_codegen_plan = _optional_dict(case, "codegen_plan")  # route 代码生成计划输入

    # 日志集合用于复现 routing 的诊断型入口场景。
    list_log_paths = _routing_log_paths(case, path_case_root)  # route 日志输入集合

    # 只读 routing 入口只返回分流决策，不应生成任何新工件。
    dict_decision = route_verilog_request(  # route_verilog_request 返回的分流决策
        request_summary=str(case.get("request_summary") or ""),  # 用户请求摘要
        rtl=path_source,  # route 输入 RTL 或缺失占位路径
        spec=path_spec_source,  # route 用来补足上下文的规格说明
        codegen_plan=dict_codegen_plan,  # 上游可能已存在的规划上下文
        logs=list_log_paths,  # route 诊断场景依赖的日志集合
        artifact_dir=path_artifact_dir,  # 上游已有工件目录
        remote_validation_requested=bool(case.get("remote_validation_requested", False)),  # 是否显式请求远程验证
    )  # route 入口决策

    # expectations 描述 route 决策里哪些字段必须命中。
    dict_expectations = _case_expectations(case)  # routing case 打开的期望字段集合

    # route 的检查项不是固定集合，要按 expectations 动态展开。
    dict_checks: dict[str, bool] = {}  # route case 动态展开的检查结果

    # recommended_flow 精确匹配时记录检查项。
    if "recommended_flow" in dict_expectations:

        # 推荐流用于区分 generation、verify、diagnosis 等入口。
        dict_checks["recommended_flow"] = (
            dict_decision.get("recommended_flow")  # 实际推荐入口
            == dict_expectations.get("recommended_flow")  # case 期望的推荐入口
        )

    # entry_mode 用来约束 UI/CLI 应该走哪种只读入口。
    if "entry_mode" in dict_expectations:

        # entry_mode 是 host 用于选择 UI/CLI shell 的稳定字段。
        dict_checks["entry_mode"] = (
            dict_decision.get("entry_mode") == dict_expectations.get("entry_mode")  # 实际入口模式是否命中期望
        )

    # missing_inputs_contains 逐项检查必须出现的缺失输入。
    for str_item in dict_expectations.get("missing_inputs_contains", []):

        # key 中保留输入名，便于失败报告直接定位。
        dict_checks[f"missing_inputs_contains_{str_item}"] = (
            str_item in dict_decision.get("missing_inputs", [])  # 当前缺失输入是否已被 route 标出
        )

    # missing_inputs_not_contains 逐项检查不应出现的缺失输入。
    for str_item in dict_expectations.get("missing_inputs_not_contains", []):

        # 负向检查防止 route 过度阻塞已有输入。
        dict_checks[f"missing_inputs_not_contains_{str_item}"] = (
            str_item not in dict_decision.get("missing_inputs", [])  # 当前输入不应被误判为缺失
        )

    # blocking_findings_contains 逐项检查必须出现的阻断发现。
    for str_item in dict_expectations.get("blocking_findings_contains", []):

        # 阻断发现用于提示 host 需要更保守的后续动作。
        dict_checks[f"blocking_findings_contains_{str_item}"] = (
            str_item in dict_decision.get("blocking_findings", [])  # 当前阻断发现是否被 route 标出
        )

    # next_action_contains 检查人类可读建议中是否包含关键短语。
    if dict_expectations.get("next_action_contains"):

        # next_action 是 UI 和命令行都会展示的恢复建议。
        dict_checks["next_action_contains"] = str(  # 人类可读建议里必须包含指定关键短语
            dict_expectations["next_action_contains"]  # 期望在建议文本中出现的关键短语
        ) in str(dict_decision.get("next_action", ""))

    # provenance 策略确保 route 不泄漏临时 reference workspace。
    dict_checks.update(  # provenance 相关的兜底检查项
        {
            "provenance_policy_present": dict_decision.get(
                "provenance_policy",
                {},
            ).get("reference_material")
            == "abstract_principles_only",
            "temporary_source_workspace_absent": "IC-" + "AGENT-HUB"
            not in json.dumps(dict_decision, ensure_ascii=False),
        }
    )

    # 未在 expectations 中启用的可选检查按旧逻辑视为通过。
    dict_checks = _enabled_checks(dict_checks, dict_expectations)  # route case 启用后的检查集合

    # routing 报告需要额外带 route_decision 便于人工诊断。
    return _report_from_checks(
        case,
        str_case_id,
        dict_checks,
        source=str(case.get("source", "")),
        with_skill_extra={"route_decision": dict_decision},
    )

# kind 到 evaluator 的分发表集中在文件末尾，避免 _evaluate_case 过长。
CASE_EVALUATORS: dict[str, CaseEvaluator] = {  # 供 _evaluate_case 按 kind 查表分派的 evaluator 注册表
    "analysis_regression": _evaluate_analysis_case,  # 核对结构分析产物的完整性
    "transform_validation_regression": _evaluate_transform_case,  # 核对 transform 辅助链路的产物
    "style_improve_regression": _evaluate_style_improve_case,  # 核对 style_improve 报告与 ready 状态
    "checkpoint_closure_regression": _evaluate_checkpoint_case,  # 核对 checkpoint 和 prompt 注入
    "generation_mode_regression": _evaluate_generation_mode_case,  # 核对 deep_review 额外 review 证据
    "streaming_regression": _evaluate_streaming_case,  # 核对流式 transcript 与 stream 标记
    "batch_regression": _evaluate_batch_case,  # 核对 batch 多 case 输出隔离
    "merge_assist_regression": _evaluate_merge_assist_case,  # 核对 merge_assist 的计划型工件
    "optimize_assist_regression": _evaluate_optimize_case,  # 核对 optimize advisory 双路径
    "verify_existing_diagnostics_regression": _evaluate_verify_existing_diagnostics_case,  # 核对 diagnostics 报告包
    "verify_existing_augment_regression": _evaluate_verify_existing_augment_case,  # 核对 augment 的保守与自动应用路径
    "verify_existing_rtl_repair_regression": _evaluate_verify_existing_rtl_repair_case,  # 核对 RTL repair 的应用边界
    "verify_existing_rtl_patch_library_regression": _evaluate_verify_existing_rtl_patch_library_case,  # 核对 patch-library 分类路径
    "routing_regression": _evaluate_routing_case,  # 核对只读 route 决策字段
    "rtl_md_constraint_regression": _evaluate_rtl_md_constraint_case,  # 核对 RTL Markdown 约束注入
}  # kind 到具体 evaluator 的查找表
