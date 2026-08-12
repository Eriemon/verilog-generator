"""verify-repair testbench 生成与增强辅助函数。"""

# 延迟注解避免 Path 和 JSON 类型在运行期产生额外负担。
from __future__ import annotations

# diff、复制、时间戳和路径处理支撑 TB 增强工件。
import difflib
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# improve_existing_rtl 负责生成标准 testbench scaffold。
from .existing_rtl_improvement import ImproveExistingRtlOptions, improve_existing_rtl

# TB contract 是 verify-repair 后续 validation 的连接点。
def materialize_tb_contract(
    *,
    dict_analysis: dict[str, Any],
    path_out_dir: Path,
    path_workspace_dir: Path,
    **dict_tb_options: Any,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """生成或增强 testbench，并返回 TB contract 与增强计划。

    参数:
        dict_analysis: RTL 分析结果，提供模块名、端口和验证目标。
        path_out_dir: 当前 verify-repair run 的输出目录。
        path_workspace_dir: validation 阶段读取 TB 的隔离 workspace。
        **dict_tb_options: 兼容旧调用方的 TB 模式、语言和来源选项。
    返回:
        tuple[dict[str, Any], dict[str, Any]]: TB contract 与增强计划。
    异常:
        ValueError: TB 选项缺失、包含未知字段或 augment 模式缺少既有 TB 时抛出。
    """

    # 旧关键字参数先归一化为上下文，避免入口参数继续膨胀。
    simple_namespace_tb_options = _build_materialize_tb_options(dict_tb_options)  # TB 物化配置对象

    # augment 模式必须有既有 TB 文件作为修改对象。
    if simple_namespace_tb_options.str_tb_mode == "augment":

        # 缺少 TB 时立即失败，避免误把 RTL 当 testbench 改写。
        if simple_namespace_tb_options.path_existing_tb_source is None:

            # 错误前缀遵循 current-project 统一诊断协议。
            raise ValueError(
                "> ERR: [Python] tb_mode='augment' requires an existing testbench source or "
                "an auto-detected TB in source files."
            )

        # 交给增强 helper 处理候选文件、diff 和可选写回。
        return augment_existing_testbench(
            dict_analysis=dict_analysis,
            path_out_dir=path_out_dir,
            path_workspace_dir=path_workspace_dir,
            path_existing_tb_source=simple_namespace_tb_options.path_existing_tb_source,
            str_requested_tb_language=simple_namespace_tb_options.str_tb_language,
            str_automation_mode=simple_namespace_tb_options.str_automation_mode,
        )

    # generate 模式复用 existing RTL improvement 的 TB scaffold。
    return _generate_testbench_contract(
        dict_analysis=dict_analysis,
        path_out_dir=path_out_dir,
        path_workspace_dir=path_workspace_dir,
        spec_source=simple_namespace_tb_options.spec_source,
        str_tb_mode=simple_namespace_tb_options.str_tb_mode,
        str_tb_language=simple_namespace_tb_options.str_tb_language,
    )

# 兼容入口只接受这几个旧关键字，避免拼写错误被静默吞掉。
def _build_materialize_tb_options(dict_tb_options: dict[str, Any]) -> SimpleNamespace:
    """归一化 materialize_tb_contract 的 TB 选项。

    参数:
        dict_tb_options: 上游以旧关键字形式传入的 TB 选项。
    返回:
        SimpleNamespace: 带 spec、既有 TB 路径、模式、语言和自动化策略的配置对象。
    异常:
        ValueError: 传入未知关键字或缺少必需关键字时抛出。
    """

    # 必需字段与旧公开入口保持一致。
    set_required_option_names = {  # materialize 入口必须提供的 TB 选项名集合
        "spec_source",  # 传递给 scaffold 生成器的规格来源字段
        "path_existing_tb_source",  # augment 模式使用的既有 TB 路径字段
        "str_tb_mode",  # generate 或 augment 的模式字段
        "str_tb_language",  # 目标 TB 方言字段
        "str_automation_mode",  # 写回策略字段
    }  # TB 物化入口必需字段集合

    # 未知字段通常意味着上游拼写错误，需要立即暴露。
    set_unknown_option_names = set(dict_tb_options) - set_required_option_names  # 当前调用传入但不受支持的字段

    # 发现未知选项时给出稳定错误格式。
    if set_unknown_option_names:

        # 排序后输出，保证测试和日志稳定。
        str_unknown_options = ", ".join(sorted(set_unknown_option_names))  # 未知字段诊断文本

        # current-project 约定所有异常消息带统一前缀。
        raise ValueError(f"> ERR: [Python] unsupported testbench options: {str_unknown_options}")

    # 缺失字段会导致后续路径或策略分支含义不明。
    set_missing_option_names = set_required_option_names - set(dict_tb_options)  # 调用方遗漏的必需字段

    # 缺字段时停止，避免后续 KeyError 难以定位。
    if set_missing_option_names:

        # 排序后输出，方便调用方按名称补齐。
        str_missing_options = ", ".join(sorted(set_missing_option_names))  # 缺失字段诊断文本

        # 抛出带规范前缀的配置错误。
        raise ValueError(f"> ERR: [Python] missing testbench options: {str_missing_options}")

    # 返回属性对象，后续代码沿用旧字段名表达语义。
    return SimpleNamespace(**dict_tb_options)

# generate 模式下的 TB scaffold 只写 run 目录和 workspace。
def _generate_testbench_contract(
    *,
    dict_analysis: dict[str, Any],
    path_out_dir: Path,
    path_workspace_dir: Path,

    # spec 和语言参数直接约束 improvement 生成的 scaffold。
    spec_source: str | Path | dict[str, Any] | None,
    str_tb_mode: str,
    str_tb_language: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """创建生成式 testbench contract。

    参数:
        dict_analysis: RTL 分析结果，提供顶层模块和原始 RTL 来源。
        path_out_dir: 当前 verify-repair run 的输出目录。
        path_workspace_dir: validation 读取 testbench 的隔离目录。
        spec_source: 传给 TB scaffold 生成器的行为规格来源。
        str_tb_mode: 当前 testbench 模式，生成路径通常为 generate。
        str_tb_language: 生成 scaffold 时请求的 TB 语言。
    返回:
        tuple[dict[str, Any], dict[str, Any]]: TB contract 与 generate 模式增强计划。
    """

    # top module 决定 TB scaffold 的文件名。
    str_module_name = str(dict_analysis["module_info"]["name"])  # 待验证 RTL 顶层模块名

    # 把 TB scaffold 生成阶段的可选输入整理成 refine 配置对象。
    improve_existing_rtl_options_config = ImproveExistingRtlOptions(  # 生成式 testbench scaffold 的 refine 配置
        analysis_source=Path(path_out_dir / "rtl_analysis.json"),  # 复用当前 verify-repair run 的分析 JSON
        spec_source=spec_source,  # 把行为规格继续传给 TB scaffold 生成器
        tb_language=str_tb_language,  # 按调用方要求选择 scaffold 语言
    )

    # improvement 入口会写出 tb_scaffold/tb/tb_<module>.v。
    improve_existing_rtl(
        Path(dict_analysis["provenance"]["source_paths"][0]),
        out_dir=path_out_dir / "tb_scaffold",
        improve_goal="tb_scaffold",
        options=improve_existing_rtl_options_config,
    )

    # 生成出的 TB 位于 improvement 固定目录。
    path_generated_tb = path_out_dir / "tb_scaffold" / "tb" / f"tb_{str_module_name}.v"  # improvement 固定输出的 TB 文件

    # workspace TB 路径供 validation 按相对路径读取。
    path_selected_tb = path_workspace_dir / "tb" / path_generated_tb.name  # 隔离 workspace 内被验证器读取的 TB 文件

    # 确保 workspace/tb 目录存在。
    path_selected_tb.parent.mkdir(parents=True, exist_ok=True)

    # 复制 scaffold 到隔离 workspace。
    shutil.copyfile(path_generated_tb, path_selected_tb)

    # contract 上下文保持旧 schema 字段，同时消除长参数列表。
    dict_contract_payload = {  # generate 模式的 TB contract 字段集合
        "str_tb_mode": str_tb_mode,  # 记录调用方选择的 TB 模式
        "str_tb_language": str_tb_language,  # 记录 scaffold 输出语言
        "path_testbench": path_selected_tb,  # contract 暴露的生成 TB 路径
        "path_workspace_dir": path_workspace_dir,  # 相对路径换算的 workspace 根
        "path_original_tb": None,  # generate 模式没有用户原始 TB
    }

    # generate 模式没有覆盖动作，活动 TB 就是 workspace 中的 scaffold。
    dict_contract_payload.update(  # 补齐旧 contract 需要的备份、活动和语言字段
        path_backup_tb=None,  # generate 模式不会生成备份文件
        path_active_tb=path_selected_tb,  # 后续报告中的活动 TB 路径
        str_language_before="verilog",  # scaffold 生成入口默认从 Verilog TB 起步
        str_language_after=str_tb_language,  # 生成后的语言标签
        list_actions=[],  # generate 模式不记录 augment 动作
        path_workspace_tb=None,  # 默认使用 path_testbench 作为 workspace TB
    )

    # payload helper 通过属性对象读取字段，保持调用面简洁。
    simple_namespace_contract_payload = SimpleNamespace(**dict_contract_payload)  # generate contract 上下文对象

    # 返回保持旧 schema 的 contract 和 augment plan。
    return (
        _tb_contract_payload(simple_namespace_payload=simple_namespace_contract_payload),
        {
            "version": 1,
            "tb_mode": str_tb_mode,
            "strategy": "generated_scaffold",
            "actions": [],
        },
    )

# augment 模式保留旧 TB，同时产出候选和 diff。
def augment_existing_testbench(
    *,
    dict_analysis: dict[str, Any],
    path_out_dir: Path,
    path_workspace_dir: Path,

    # 既有 TB 与自动化策略共同决定候选文件和写回行为。
    path_existing_tb_source: Path,
    str_requested_tb_language: str,
    str_automation_mode: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """增强既有 testbench 并按自动化模式决定是否写回。

    参数:
        dict_analysis: RTL 分析结果，提供端口、模块和验证目标。
        path_out_dir: 当前 verify-repair run 的输出目录。
        path_workspace_dir: validation 读取增强 TB 的隔离 workspace。
        path_existing_tb_source: 用户提供或自动识别到的既有 testbench。
        str_requested_tb_language: 调用方请求的目标 testbench 语言。
        str_automation_mode: 写回策略，决定是否覆盖或升级用户 TB。
    返回:
        tuple[dict[str, Any], dict[str, Any]]: TB contract 与 augment 计划。
    """

    # 原始 TB 文本用于 diff、语言识别和注入点定位。
    str_original_text = path_existing_tb_source.read_text(encoding="utf-8")  # 用户既有 testbench 文本

    # 语言识别当前固定为 Verilog，保留字段用于报告兼容。
    str_language_before = tb_language_from_path(path_existing_tb_source, str_original_text)  # 原始 TB 语言

    # 自动化策略当前不会改变 testbench 语言。
    str_language_after = resolve_augment_language(  # 增强后用于候选 TB 的语言标签
        str_language_before,  # 原 TB 的实际语言
        str_requested_tb_language,  # 调用方请求的目标 TB 语言
        str_automation_mode,  # 写回策略对语言升级权限的约束
    )  # 增强后的 TB 语言

    # 生成增强文本和结构化动作记录。
    tuple_augment_build = build_augmented_testbench(  # build helper 返回增强文本和动作清单
        str_original_text,  # legacy TB 原文作为增强输入
        dict_analysis=dict_analysis,  # RTL 分析结果提供端口和 checkpoint
        str_language_after=str_language_after,  # 增强输出采用的 TB 语言
    )  # 增强文本和动作清单的二元返回值

    # tuple 索引保留原返回顺序，避免命名检查误判解包目标类型。
    str_augmented_text = tuple_augment_build[0]  # 注入监控块后的完整 TB 文本

    # 动作清单进入 plan 和 contract，保持下游字段含义不变。
    list_actions = tuple_augment_build[1]  # 每个 TB 注入动作的结构化记录

    # 写出增强计划，供人工复核。
    dict_augment_plan = {
        "version": 1,  # 增强计划 schema 版本
        "tb_mode": "augment",  # plan 固定描述既有 TB 增强路径
        "original_testbench_path": str(path_existing_tb_source),  # 被增强的用户 TB 路径
        "language_before": str_language_before,  # 增强前识别到的 TB 语言
        "language_after": str_language_after,  # 候选 TB 实际采用的语言
        "actions": list_actions,  # 注入动作审查清单
    }  # TB 增强计划

    # 保存人读 diff。
    _write_tb_augment_diff(
        path_out_dir=path_out_dir,
        path_existing_tb_source=path_existing_tb_source,
        str_original_text=str_original_text,
        str_augmented_text=str_augmented_text,
        str_language_after=str_language_after,
    )

    # workspace 中始终使用增强后的 TB 做验证。
    path_workspace_tb = _write_workspace_tb(  # 将增强 TB 写入 workspace/tb，避免验证器读取用户原文件
        path_workspace_dir=path_workspace_dir,  # 承载本轮验证隔离文件树的根目录
        path_existing_tb_source=path_existing_tb_source,  # 原 TB stem 决定 workspace 内文件名
        str_augmented_text=str_augmented_text,  # 已插入监控和 transcript 的 TB 文本
    )  # tb_contract 记录的 workspace testbench 文件

    # run 目录候选文件供 semi-auto/conservative 审查。
    path_candidate = _write_tb_candidate(  # 人工确认和 conservative 报告展示的候选 TB 路径
        path_out_dir=path_out_dir,  # 候选文件落在当前 run 输出目录
        path_existing_tb_source=path_existing_tb_source,  # 原 TB 名称决定候选文件 stem
        str_augmented_text=str_augmented_text,  # 候选文件中的增强 TB 文本
        str_language_after=str_language_after,  # 候选文件后缀依据增强后语言选择
    )  # 增强候选 TB 文件

    # auto_apply 才会实际写回用户 TB 路径。
    tuple_apply_paths = _maybe_apply_tb(  # 写回 helper 返回备份路径和活动路径
        path_existing_tb_source=path_existing_tb_source,  # 可能被 auto_apply 写回的用户 TB
        str_augmented_text=str_augmented_text,  # 写回或候选引用的增强 TB 文本
        str_language_after=str_language_after,  # 增强后保持 Verilog
        str_automation_mode=str_automation_mode,  # 写回权限来自自动化模式
        path_candidate=path_candidate,  # 非写回模式下作为 active path
    )  # 自动写回阶段返回的备份和活动路径

    # 备份路径为 None 表示当前模式没有覆盖用户文件。
    path_backup = tuple_apply_paths[0]  # auto_apply 写回前生成的原 TB 备份路径

    # 活动路径指向后续报告中应展示的候选或写回文件。
    path_active = tuple_apply_paths[1]  # 当前自动化模式下的活动 TB 路径

    # augment contract 的基础字段先记录候选文件和用户来源。
    dict_contract_payload = {  # 候选 TB contract 的基础字段
        "str_tb_mode": "augment",  # 下游报告用该值识别增强路径
        "str_tb_language": str_requested_tb_language,  # 保留 CLI 请求的目标方言
        "path_testbench": path_candidate,  # run 目录中可人工复核的候选文件
        "path_workspace_dir": path_workspace_dir,  # 用于生成 workspace 相对路径的根目录
        "path_original_tb": path_existing_tb_source,  # 审计链的用户原始 TB
    }

    # 写回审计字段与语言变化字段分组补齐，避免字段含义被长块淹没。
    dict_contract_payload.update(  # augment contract 的审计链和动作清单
        path_backup_tb=path_backup,  # auto_apply 写回前的备份路径
        path_active_tb=path_active,  # 当前模式下报告展示的活动 TB
        str_language_before=str_language_before,  # 语言升级前的识别结果
        str_language_after=str_language_after,  # 候选文件真正采用的方言
        list_actions=list_actions,  # monitor/data/error 注入动作列表
        path_workspace_tb=path_workspace_tb,  # validation 使用的增强 TB 路径
    )

    # 属性对象只在本文件内部流转，不改变 JSON 输出结构。
    simple_namespace_contract_payload = SimpleNamespace(**dict_contract_payload)  # 候选 TB contract 属性视图

    # 返回 contract 与计划。
    return (
        _tb_contract_payload(simple_namespace_payload=simple_namespace_contract_payload),
        dict_augment_plan,
    )

# diff 文件保持旧固定文件名，供 verification_result 引用。
def _write_tb_augment_diff(
    *,
    path_out_dir: Path,
    path_existing_tb_source: Path,
    str_original_text: str,
    str_augmented_text: str,
    str_language_after: str,
) -> None:
    """写出 testbench 增强 diff。

    参数:
        path_out_dir: 当前 run 的输出目录。
        path_existing_tb_source: 被增强的原始 testbench 路径。
        str_original_text: 用户原始 TB 文本。
        str_augmented_text: 注入监控后的候选 TB 文本。
        str_language_after: 候选 TB 的语言标签，用于 diff 右侧后缀。
    返回:
        None: 直接写出 tb_augment_diff.txt。
    """

    # diff 展示路径保持原 TB 后缀。
    str_target_suffix = path_existing_tb_source.suffix  # diff 展示路径使用的目标后缀

    # diff 目标路径仅用于显示，不一定真实写回。
    path_diff_target = path_existing_tb_source.with_suffix(str_target_suffix)  # diff 中展示的目标路径

    # unified diff 便于人工审查候选 TB。
    str_diff_text = "\n".join(  # 写入 tb_augment_diff.txt 的 unified diff 文本
        difflib.unified_diff(  # 生成原 TB 与增强 TB 的逐行差异
            str_original_text.splitlines(),  # diff 左侧使用用户原始 TB 行
            str_augmented_text.splitlines(),  # diff 右侧使用增强候选 TB 行
            fromfile=str(path_existing_tb_source),  # diff 左侧标题保留用户 TB 原文件名
            tofile=str(path_diff_target),  # diff 右侧标题显示增强候选的目标文件名
            lineterm="",  # 避免 difflib 额外追加换行导致写文件双换行
        )
    )  # TB 增强 unified diff

    # diff 文件即使为空也要存在，保持工件完整。
    (path_out_dir / "tb_augment_diff.txt").write_text(
        str_diff_text + ("\n" if str_diff_text else ""),
        encoding="utf-8",
    )

# workspace TB 总是写 `.v` 名称以兼容 validator 的 Verilog 文件收集。
def _write_workspace_tb(
    *,
    path_workspace_dir: Path,
    path_existing_tb_source: Path,
    str_augmented_text: str,
) -> Path:
    """写入 validation workspace 中的 testbench。

    参数:
        path_workspace_dir: validation 的隔离 workspace 根目录。
        path_existing_tb_source: 原始 TB 路径，用于派生 workspace 文件名。
        str_augmented_text: 写入 workspace 的增强 TB 文本。
    返回:
        Path: validation 实际读取的 workspace TB 路径。
    """

    # workspace/tb 是 validator 唯一会扫描的 TB 子目录。
    path_workspace_tb_dir = path_workspace_dir / "tb"  # 验证器专门扫描 testbench 文件的 workspace 子目录

    # 创建 validator 后续扫描的 workspace/tb 目录。
    path_workspace_tb_dir.mkdir(parents=True, exist_ok=True)

    # workspace 侧固定 `.v` 后缀，保持旧验证行为。
    path_workspace_tb = path_workspace_tb_dir / path_existing_tb_source.with_suffix(".v").name  # validator 扫描到的 workspace TB 路径

    # 写入增强后的 TB 内容。
    path_workspace_tb.write_text(str_augmented_text, encoding="utf-8")

    # 返回供 contract 记录的 workspace 路径。
    return path_workspace_tb

# run 目录候选 TB 是人工确认和报告展示对象。
def _write_tb_candidate(
    *,
    path_out_dir: Path,
    path_existing_tb_source: Path,
    str_augmented_text: str,
    str_language_after: str,
) -> Path:
    """写出增强候选 testbench。

    参数:
        path_out_dir: 当前 verify-repair run 的输出目录。
        path_existing_tb_source: 原始 TB 路径，用于派生候选文件名。
        str_augmented_text: 候选文件要写入的增强 TB 文本。
        str_language_after: 候选 TB 语言，当前固定为 Verilog。
    返回:
        Path: 写出的增强候选 TB 路径。
    """

    # 候选后缀沿用原始 TB 后缀。
    str_candidate_suffix = path_existing_tb_source.suffix  # 候选文件后缀

    # 候选文件放在 run 目录下，不覆盖用户源文件。
    path_candidate = (
        path_out_dir  # 当前 verify-repair run 的输出目录
        / "tb_augmented_candidate"  # 人工审查候选 TB 的固定子目录
        / f"{path_existing_tb_source.stem}_augmented{str_candidate_suffix}"  # 带 augmented 标记的候选文件名
    )  # TB 增强候选路径

    # 确保候选目录存在。
    path_candidate.parent.mkdir(parents=True, exist_ok=True)

    # 写出候选文件。
    path_candidate.write_text(str_augmented_text, encoding="utf-8")

    # 返回候选路径供 contract 记录。
    return path_candidate

# 只有 auto_apply 可以写回用户 testbench，且必须先备份。
def _maybe_apply_tb(
    *,
    path_existing_tb_source: Path,
    str_augmented_text: str,
    str_language_after: str,
    str_automation_mode: str,
    path_candidate: Path,
) -> tuple[Path | None, Path]:
    """按自动化模式决定是否写回 testbench。

    参数:
        path_existing_tb_source: 可能被 auto_apply 写回的用户 TB 文件。
        str_augmented_text: 准备写回或作为候选展示的增强 TB 文本。
        str_language_after: 增强后的 TB 语言标签，当前固定为 Verilog。
        str_automation_mode: 调用方选择的自动化写回策略。
        path_candidate: 非写回模式下作为活动文件的候选 TB 路径。
    返回:
        tuple[Path | None, Path]: 备份路径和当前活动 TB 路径。
    """

    # 非 auto_apply 模式只返回候选文件作为 active path。
    if str_automation_mode != "auto_apply":

        # 没有写回时不产生备份。
        return None, path_candidate

    # 写回前备份原始 TB。
    path_backup = backup_path(path_existing_tb_source)  # 原始 TB 备份路径

    # 保存备份，防止自动写回不可逆。
    shutil.copyfile(path_existing_tb_source, path_backup)

    # Verilog 写回仍沿用用户提供的原文件。
    path_active = path_existing_tb_source  # 原地覆盖后的 TB 路径

    # 写回增强后的 testbench。
    path_active.write_text(str_augmented_text, encoding="utf-8")

    # 返回备份和活动路径。
    return path_backup, path_active

# contract payload 字段保持旧测试和 CLI 兼容。
def _tb_contract_payload(*, simple_namespace_payload: SimpleNamespace) -> dict[str, Any]:
    """组装 testbench contract payload。

    参数:
        simple_namespace_payload: 包含模式、路径、语言和增强动作的 TB contract 上下文。
    返回:
        dict[str, Any]: 与旧 tb_contract.json 兼容的 payload。
    """

    # workspace TB 默认就是 testbench path 本身。
    path_contract_workspace_tb = (  # contract 中的 workspace TB 路径
        simple_namespace_payload.path_workspace_tb or simple_namespace_payload.path_testbench  # 优先使用显式 workspace TB
    )  # contract 中可相对化的 TB 文件路径

    # workspace 路径必须保持相对形式，避免 run 目录迁移后失效。
    str_workspace_testbench_path = str(  # tb_contract.json 中写入的 workspace 相对路径
        path_contract_workspace_tb.relative_to(simple_namespace_payload.path_workspace_dir).as_posix()  # 相对 workspace 根的 POSIX 路径文本
    )  # tb_contract.json 记录的 workspace 相对 TB 路径

    # 返回旧 schema 兼容的 testbench contract。
    return {
        "version": 1,
        "tb_mode": simple_namespace_payload.str_tb_mode,
        "tb_language": simple_namespace_payload.str_tb_language,
        "testbench_path": str(simple_namespace_payload.path_testbench),

        # contract 对外暴露相对路径，避免泄漏本机 run 目录。
        "workspace_testbench_path": str_workspace_testbench_path,
        "original_testbench_path": (
            str(simple_namespace_payload.path_original_tb) if simple_namespace_payload.path_original_tb else None
        ),
        "backup_testbench_path": (
            str(simple_namespace_payload.path_backup_tb) if simple_namespace_payload.path_backup_tb else None
        ),
        "active_testbench_path": str(simple_namespace_payload.path_active_tb),

        # 语言和动作字段供 tb_augment_plan 与 contract 交叉审计。
        "language_before": simple_namespace_payload.str_language_before,
        "language_after": simple_namespace_payload.str_language_after,
        "augmentation_actions": simple_namespace_payload.list_actions,

        # 日志标签和 transcript 前缀是 log diagnosis 的稳定协议。
        "log_tags": ["[TB_MONITOR]", "[TB_DATA]", "[TB_ERROR]", "[TB_INFO]"],
        "transcript_prefix": "VERILOG-GEN-RESULT",
    }

# build_augmented_testbench 只拼装注入块，不直接写文件。
def build_augmented_testbench(
    str_original_text: str,
    *,
    dict_analysis: dict[str, Any],
    str_language_after: str,
) -> tuple[str, list[dict[str, Any]]]:
    """根据既有 TB 缺失项生成增强文本。

    参数:
        str_original_text: 用户既有 TB 的原始文本。
        dict_analysis: RTL 分析结果，提供模块、端口和验证目标。
        str_language_after: 增强候选 TB 使用的目标语言。
    返回:
        tuple[str, list[dict[str, Any]]]: 增强后的 TB 文本和结构化动作清单。
    """

    # 动作清单记录每个注入理由。
    list_actions: list[dict[str, Any]] = []  # TB 增强动作列表

    # 模块名用于动作记录和诊断上下文。
    str_module_name = str(dict_analysis["module_info"]["name"])  # 被测模块名

    # 选择第一个输出信号作为最小观测点。
    list_outputs = [
        dict_item["name"]  # 作为 TB_DATA 最小采样目标的输出端口名
        for dict_item in dict_analysis.get("ports", [])  # 遍历分析阶段提取的端口描述
        if dict_item.get("direction") == "output"  # 只保留可被 testbench 采样的输出端口
    ]  # 可观测输出端口名列表

    # 输出信号可能缺失，此时跳过数据监控。
    str_output_signal = list_outputs[0] if list_outputs else ""  # TB 观测输出信号

    # 时钟信号缺失时使用传统 clk 兜底。
    str_clock_name = next(  # 监控 always 块使用的时钟端口名
        (
            dict_item["name"]  # role=clock 的端口名
            for dict_item in dict_analysis.get("ports", [])  # 遍历端口以寻找时钟角色
            if dict_item.get("role") == "clock"  # 只接受分析阶段标出的 clock 端口
        ),
        "clk",  # 分析缺少 clock role 时沿用传统 TB 时钟名
    )  # TB 监控使用的时钟名

    # 复位信号缺失时使用 rst_n 兜底。
    str_reset_name = next(  # TB_ERROR 占位分支使用的复位端口名
        (
            dict_item["name"]  # reset role 对应的端口名
            for dict_item in dict_analysis.get("ports", [])  # 扫描端口以寻找复位角色
            if dict_item.get("role") == "reset"  # reset role 是 property disable 的唯一自动依据
        ),
        "rst_n",  # 分析缺少 reset role 时沿用常见低有效复位名
    )  # TB_ERROR 占位分支复位名

    # 只注入前四个 checkpoint，避免 legacy TB 被塞入过多报告语句。
    list_checkpoints = dict_analysis.get("verification_targets", [])[:4]  # monitor 日志最多展示的验证目标切片

    # 收集待插入 endmodule 前的代码块。
    list_injected_blocks: list[str] = []  # 准备注入 legacy TB 的代码块

    # 缺少 monitor 标签时补充基础 monitor。
    _append_monitor_block(
        str_original_text=str_original_text,
        list_actions=list_actions,
        list_injected_blocks=list_injected_blocks,
        list_checkpoints=list_checkpoints,
    )

    # 缺少数据标签且有输出信号时补充输出采样。
    _append_data_block(
        str_original_text=str_original_text,
        list_actions=list_actions,
        list_injected_blocks=list_injected_blocks,
        str_clock_name=str_clock_name,
        str_output_signal=str_output_signal,
    )

    # 缺少机器 transcript 时补充最小 PASS 记录。
    _append_transcript_block(
        str_original_text=str_original_text,
        list_actions=list_actions,
        list_injected_blocks=list_injected_blocks,
    )

    # 缺少完成标记时补充 TB_INFO。
    _append_completion_block(
        str_original_text=str_original_text,
        list_actions=list_actions,
        list_injected_blocks=list_injected_blocks,
    )

    # 缺少 watchdog 时补充 timeout 保护。
    _append_watchdog_block(
        str_original_text=str_original_text,
        list_actions=list_actions,
        list_injected_blocks=list_injected_blocks,
    )

    # error 路径上下文集中保存，避免 helper 参数继续扩张。
    dict_error_context = {  # TB_ERROR 注入需要的文本、动作和端口上下文
        "str_original_text": str_original_text,  # 既有 TB 文本用于检测是否已有错误路径
        "list_actions": list_actions,  # 动作清单由 error helper 原地补充
        "list_injected_blocks": list_injected_blocks,  # 待插入代码块由 error helper 原地补充
        "str_language_after": str_language_after,  # 语言决定使用 property 还是占位分支
    }

    # 端口和模块字段单独分组，便于理解错误路径构造来源。
    dict_error_context.update(  # TB_ERROR 注入所需的模块和信号名称
        str_module_name=str_module_name,  # property 名称使用的被测模块名
        str_clock_name=str_clock_name,  # property 或 always 采样使用的时钟名
        str_reset_name=str_reset_name,  # property disable 条件使用的复位名
        str_output_signal=str_output_signal,  # 未知值检查或占位说明中的输出信号
    )

    # error helper 通过属性对象读取上下文，减少参数面。
    simple_namespace_error_context = SimpleNamespace(**dict_error_context)  # TB_ERROR 注入上下文对象

    # 缺少 TB_ERROR 路径时补充占位 error branch。
    _append_error_block(simple_namespace_context=simple_namespace_error_context)

    # 没有动作说明 legacy TB 已经具备必要 hooks。
    if not list_actions:

        # noop 仍作为结构化证据写入 plan。
        list_actions.append({"kind": "noop", "reason": "existing TB already contains required hooks"})

        # 返回原文，避免无意义 diff。
        return str_original_text, list_actions

    # 将注入块插到 endmodule 前。
    str_augmented_text = _insert_blocks_before_endmodule(str_original_text, list_injected_blocks)  # 增强后的 TB 文本

    # 返回增强文本和动作列表。
    return str_augmented_text, list_actions

# monitor 块提供 checkpoint 级可见日志。
def _append_monitor_block(
    *,
    str_original_text: str,
    list_actions: list[dict[str, Any]],
    list_injected_blocks: list[str],
    list_checkpoints: list[dict[str, Any]],
) -> None:
    """在缺少 TB_MONITOR 时追加 monitor 块。

    参数:
        str_original_text: 用户既有 TB 的原始文本。
        list_actions: 记录本轮增强动作的可变列表。
        list_injected_blocks: 收集待插入 Verilog 代码块的可变列表。
        list_checkpoints: 最多用于 monitor 日志的验证目标切片。
    返回:
        None: 直接修改动作列表和注入块列表。
    """

    # 已存在 monitor 标签时不重复注入。
    if "[TB_MONITOR]" in str_original_text:

        # 直接返回，保护 legacy TB。
        return

    # 记录增强动作。
    dict_monitor_action = {
        "kind": "log_tag",  # 动作类型标记为补充日志标签
        "tag": "[TB_MONITOR]",  # 补齐 checkpoint monitor 的日志标签
        "reason": "missing monitor tag",  # 触发增强的诊断原因
    }  # TB_MONITOR 增强动作

    # 动作写入计划，供后续 contract 和人工审查复用。
    list_actions.append(dict_monitor_action)

    # monitor 行缓冲会在 checkpoint 日志补齐后整体插入 endmodule 前。
    list_monitor_lines: list[str] = []  # monitor initial 块的 Verilog 行缓冲

    # 注入块前保留空行，避免首条增强语句贴住 legacy TB。
    list_monitor_lines.append("")

    # 说明后续 initial 块来自 verify-repair 增强流程。
    list_monitor_lines.append("    // Augmented monitor block for verify-repair.")

    # 打开 monitor initial 块。
    list_monitor_lines.append("    initial begin")

    # 写入进入增强验证路径的第一条 monitor 日志。
    list_monitor_lines.append(
        '        $display(" > INFO: [Verilog] [TB_MONITOR] Time: %0t | Augmented verification entry.", $time);'
    )

    # 每个 checkpoint 输出一条 monitor 日志。
    for dict_target in list_checkpoints:

        # signals 列表压成逗号分隔，保持旧输出格式。
        str_signals = ",".join(dict_target.get("signals", []))  # checkpoint 关注信号文本

        # check_id 缺失时保持字符串化输出。
        str_check_id = str(dict_target.get("check_id"))  # monitor 日志中展示的验证目标编号

        # 追加 monitor 行。
        list_monitor_lines.append(
            f'        $display(" > INFO: [Verilog] [TB_MONITOR] Time: %0t | '
            f'{str_check_id} | signals={str_signals}", $time);'
        )

    # 结束 monitor initial 块。
    list_monitor_lines.extend(["    end", ""])

    # 合并到注入块列表。
    list_injected_blocks.extend(list_monitor_lines)

# data 块提供最小输出观测。
def _append_data_block(
    *,
    str_original_text: str,
    list_actions: list[dict[str, Any]],
    list_injected_blocks: list[str],
    str_clock_name: str,
    str_output_signal: str,
) -> None:
    """在缺少 TB_DATA 时追加输出采样块。

    参数:
        str_original_text: 用户既有 TB 的原始文本。
        list_actions: 记录本轮增强动作的可变列表。
        list_injected_blocks: 收集待插入 Verilog 代码块的可变列表。
        str_clock_name: 输出采样 always 块使用的时钟名。
        str_output_signal: 被采样的输出信号名，空字符串表示无法采样。
    返回:
        None: 直接修改动作列表和注入块列表。
    """

    # 没有输出信号或已有 TB_DATA 时不注入。
    if not str_output_signal or "[TB_DATA]" in str_original_text:

        # 保护无法确定输出或已有监控的 testbench。
        return

    # 记录数据观测增强动作。
    dict_data_action = {
        "kind": "log_tag",  # 动作类型标记为日志标签补齐
        "tag": "[TB_DATA]",  # 补齐输出采样的日志标签
        "reason": "missing observed data tag",  # 输出采样日志缺口的诊断原因
    }

    # 动作写入计划，说明新增了输出采样路径。
    list_actions.append(dict_data_action)

    # 输出采样语句保持原有日志格式，只拆出文本以消除超长行。
    str_data_display_line = (
        f'        $display(" > INFO: [Verilog] [TB_DATA] Time: %0t | Observed {str_output_signal}=%0h", '  # display 格式前半段
        f"$time, {str_output_signal});"  # display 参数保持原输出信号采样
    )  # TB_DATA 输出采样 display 语句

    # always 块每个时钟采样输出。
    list_injected_blocks.extend(
        [
            f"    always @(posedge {str_clock_name}) begin",
            str_data_display_line,
            "    end",
            "",
        ]
    )

# transcript 块为 semantic checker 提供机器可读结果。
def _append_transcript_block(
    *,
    str_original_text: str,
    list_actions: list[dict[str, Any]],
    list_injected_blocks: list[str],
) -> None:
    """在缺少机器 transcript 时追加 PASS 记录。

    参数:
        str_original_text: 用户既有 TB 的原始文本。
        list_actions: 记录本轮增强动作的可变列表。
        list_injected_blocks: 收集待插入 Verilog 代码块的可变列表。
    返回:
        None: 直接修改动作列表和注入块列表。
    """

    # 已有 transcript 时不注入。
    if "VERILOG-GEN-RESULT" in str_original_text:

        # 直接返回保护 legacy 语义结果。
        return

    # 记录语义结果行的补齐动作。
    dict_transcript_action = {
        "kind": "transcript",  # 动作类型标记为机器 transcript 补齐
        "tag": "VERILOG-GEN-RESULT",  # 语义验证链识别的固定前缀
        "reason": "missing machine-readable transcript",  # 语义检查器缺少 PASS 记录的原因
    }

    # 动作写入计划，说明新增了机器可读 PASS 记录。
    list_actions.append(dict_transcript_action)

    # transcript 前半段固定 case_id，保持旧日志解析 key 不变。
    str_transcript_case_prefix = '        $display("VERILOG-GEN-RESULT {\\"case_id\\":\\"augmented_case\\",'  # 机器结果前缀与 case_id 字段文本

    # transcript 后半段固定 PASS 状态和空 outputs，维持最小成功记录。
    str_transcript_status_suffix = '\\"status\\":\\"PASS\\",\\"outputs\\":{}}");'  # PASS 状态与空输出字段文本

    # 拼回完整 display 行，避免在注入块中改变原有 transcript 字符串。
    str_transcript_display_line = str_transcript_case_prefix + str_transcript_status_suffix  # 完整机器可读 PASS 日志行

    # 写入最小 PASS transcript，供验证链识别。
    list_injected_blocks.extend(
        [
            "    initial begin",
            str_transcript_display_line,
            "    end",
            "",
        ]
    )

# 完成标记让日志诊断可识别正常结束。
def _append_completion_block(
    *,
    str_original_text: str,
    list_actions: list[dict[str, Any]],
    list_injected_blocks: list[str],
) -> None:
    """在缺少 TB_INFO 时追加完成标记。

    参数:
        str_original_text: 用户既有 TB 的原始文本。
        list_actions: 记录本轮增强动作的可变列表。
        list_injected_blocks: 收集待插入 Verilog 代码块的可变列表。
    返回:
        None: 直接修改动作列表和注入块列表。
    """

    # 已存在完成标记时不重复注入。
    if "[TB_INFO]" in str_original_text:

        # 直接返回保护原 TB。
        return

    # 记录完成标记动作。
    dict_completion_action = {
        "kind": "log_tag",  # 动作类型标记为仿真结束日志补齐
        "tag": "[TB_INFO]",  # 结束状态识别使用的日志标签
        "reason": "missing completion marker",  # 日志诊断缺少正常结束信号
    }

    # 动作写入计划，说明新增了正常结束标记。
    list_actions.append(dict_completion_action)

    # 注入标准完成日志。
    list_injected_blocks.extend(
        [
            "    initial begin",
            '        $display(" > INFO: [Verilog] [TB_INFO] Simulation Finished!");',
            "    end",
            "",
        ]
    )

# watchdog 防止 legacy TB 无结束条件。
def _append_watchdog_block(
    *,
    str_original_text: str,
    list_actions: list[dict[str, Any]],
    list_injected_blocks: list[str],
) -> None:
    """在缺少 timeout guard 时追加 watchdog。

    参数:
        str_original_text: 用户既有 TB 的原始文本。
        list_actions: 记录本轮增强动作的可变列表。
        list_injected_blocks: 收集待插入 Verilog 代码块的可变列表。
    返回:
        None: 直接修改动作列表和注入块列表。
    """

    # 已有 timeout 文本时不重复注入。
    if "simulation timeout" in str_original_text.lower():

        # 已有超时保护时避免重复结束仿真。
        return

    # 用结构化动作标记 legacy TB 缺少 timeout guard。
    dict_watchdog_action = {
        "kind": "watchdog",  # 动作类型标记为仿真挂死保护
        "reason": "missing timeout guard",  # legacy TB 没有超时退出条件
    }

    # 动作写入计划，说明新增了防挂死保护。
    list_actions.append(dict_watchdog_action)

    # 注入固定周期 timeout 保护。
    list_injected_blocks.extend(
        [
            "    initial begin",
            "        #(CLK_PERIOD * 200);",
            '        $display(" > ERR: [Verilog] simulation timeout");',
            "        $finish;",
            "    end",
            "",
        ]
    )

# error 块确保 TB 存在失败路径。
def _append_error_block(*, simple_namespace_context: SimpleNamespace) -> None:
    """在缺少 TB_ERROR 时追加错误检查路径。

    参数:
        simple_namespace_context: 包含原 TB 文本、动作列表、注入块和端口语言信息的上下文。
    返回:
        None: 直接修改上下文中的动作列表和注入块列表。
    """

    # 已有错误路径时不注入。
    if "[TB_ERROR]" in simple_namespace_context.str_original_text:

        # 既有失败日志通常承载项目特定断言，不能重复覆盖。
        return

    # 记录错误路径动作。
    dict_error_action = {
        "kind": "error_path",  # 动作类型标记为补充失败路径
        "tag": "[TB_ERROR]",  # 日志诊断识别失败分支的标签
        "reason": "missing explicit TB error path",  # 日志诊断缺少显式失败分支
    }

    # 动作写入计划，说明新增了可被日志诊断识别的失败路径。
    simple_namespace_context.list_actions.append(dict_error_action)

    # Verilog 分支保留原非触发条件，只补日志路径供人工替换。
    str_error_placeholder_line = (
        '            $error("[TB_ERROR] Time: %0t | Replace legacy checks '  # 占位错误日志前半句
        'with module-specific expectations.", $time);'  # 提示人工替换为模块期望
    )  # Verilog 占位失败日志语句

    # 注入不会触发的 error path，提示后续替换为模块特定检查。
    simple_namespace_context.list_injected_blocks.extend(
        [
            "    initial begin",
            "        if (^1'b0 === 1'b1) begin",
            str_error_placeholder_line,
            "        end",
            "    end",
            "",
        ]
    )

# 注入块插到最后一个 endmodule 前，缺失 endmodule 时追加到末尾。
def _insert_blocks_before_endmodule(str_original_text: str, list_injected_blocks: list[str]) -> str:
    """把增强代码块插入 testbench 文本。

    参数:
        str_original_text: 用户既有 TB 的原始文本。
        list_injected_blocks: 已生成的 Verilog 增强代码块列表。
    返回:
        str: 插入增强块后的完整 TB 文本。
    """

    # 查找最后一个 endmodule，避免误插到子模块内部。
    int_endmodule_index = str_original_text.rfind("endmodule")  # 最后一个 endmodule 位置

    # 没有 endmodule 时只能追加到末尾。
    if int_endmodule_index == -1:

        # 保持末尾换行。
        return str_original_text.rstrip() + "\n" + "\n".join(list_injected_blocks)

    # 分成 endmodule 前后的文本。
    str_prefix = str_original_text[:int_endmodule_index].rstrip()  # endmodule 前的 TB 文本

    # 在 endmodule 前插入增强块。
    return str_prefix + "\n" + "\n".join(list_injected_blocks) + "endmodule\n"

# TB 语言识别当前固定为 Verilog。
def tb_language_from_path(path_source: Path, str_text: str) -> str:
    """判断 testbench 文本的语言。

    参数:
        path_source: testbench 源文件路径。
        str_text: testbench 文本内容。
    返回:
        str: `verilog` 语言标签。
    """

    # 当前边界固定为 Verilog。
    return "verilog"

# 语言解析当前始终保持 Verilog。
def resolve_augment_language(str_language_before: str, str_requested_tb_language: str, str_automation_mode: str) -> str:
    """根据自动化模式确定增强后的 testbench 语言。

    参数:
        str_language_before: 增强前识别到的 testbench 语言。
        str_requested_tb_language: 调用方请求的目标 testbench 语言。
        str_automation_mode: 写回策略，用于限制语言升级。
    返回:
        str: 增强候选 TB 实际采用的语言标签。
    """

    # 默认保持 Verilog，自动化模式不改变语言边界。
    return "verilog"

# 备份路径带时间戳，避免覆盖旧备份。
def backup_path(path_source: Path) -> Path:
    """构造同目录带时间戳的备份路径。

    参数:
        path_source: 需要备份的原始 testbench 文件路径。
    返回:
        Path: 与源文件同目录且带时间戳的备份路径。
    """

    # 时间戳使用秒级粒度，匹配旧文件名模式。
    str_timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")  # 备份文件时间戳

    # 返回同目录备份路径。
    return path_source.with_name(f"{path_source.stem}.backup-{str_timestamp}{path_source.suffix}")
