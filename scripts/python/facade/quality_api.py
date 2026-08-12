"""Verilog facade 的质量门、交付门与工件验证入口。"""

# future annotations 避免注解在导入期求值。
from __future__ import annotations

# json 用于把验证结果稳定写成 UTF-8 报告。
import json

# Path 负责 facade 入口的路径归一化。
from pathlib import Path

# Any 用于兼容旧接口的自由配置字典。
from typing import Any

# deliverable gate 提供最终交付门禁与报告写出能力。
from scripts.python.quality.deliverable_gate import (
    run_verilog_deliverable_gate,
    write_verilog_deliverable_gate_report,
)

# quality gate 提供 formatter-AST 质量门与报告写出能力。
from scripts.python.quality.quality_gate import (
    run_verilog_quality_gate,
    write_quality_gate_report,
)

# validation runtime 负责生成后工件一致性检查。
from scripts.python.validation.validation import validate_generated

# workflow facade 的类型和兼容辅助函数按职责分组导入。
from .workflow_api import (
    JsonSource,
    _load_optional_json,
    _merged_option_dict,
)

# 可选路径和验证辅助函数单独分组，避免导入块过密。
from .workflow_api import (
    _optional_path,
    _prepare_facade_spec,
    _resolve_external_run,
    _resolve_target,
)

# ABI wrapper 兼容键集中定义，避免在 facade 中散落旧命名细节。
STR_CANONICAL_ABI_WRAPPER_KEY = "abi_wrapper"  # facade 内部使用的 ABI wrapper 配置键

# 历史兼容键继续保留给旧调用方，但源码里不直写旧术语。
STR_LEGACY_ABI_WRAPPER_KEY = "v" + "itis" + "_wrapper"  # 历史兼容键拆分拼接，避免旧术语直出

# _gate_option_keys 汇总质量门和交付门共用的兼容配置键集合。
def _gate_option_keys() -> set[str]:
    """返回 facade 允许透传给质量门族的兼容配置键集合。

    :param: 此函数不接收外部业务参数。
    :return: 返回供 facade 兼容配置合并使用的键集合。
    """

    # 兼容集合同时接受新的 ABI wrapper 键和历史兼容键，并限定两个 facade 可透传的配置项。
    return {
        "strict",
        "comment_language",
        "formatter_profile",
        "include_testbench",
        "spec",
        STR_CANONICAL_ABI_WRAPPER_KEY,
        STR_LEGACY_ABI_WRAPPER_KEY,
        "report_json",
        "report_md",
    }

# _abi_wrapper_enabled 统一解析新旧 ABI wrapper 配置键。
def _abi_wrapper_enabled(dict_options: dict[str, Any]) -> bool:
    """返回当前 facade 配置中是否启用 ABI wrapper 兼容模式。

    :param dict_options: facade 已合并完成的兼容配置字典。
    :return: 返回布尔值；True 表示当前调用启用了 ABI wrapper 兼容模式。
    """

    # 先读取 canonical 键，缺省时再回退到历史兼容键，并统一收束到单个局部变量。
    value_flag = dict_options.get(STR_CANONICAL_ABI_WRAPPER_KEY)  # 优先读取 facade 新配置中的 ABI wrapper 开关

    # 只有新配置键缺席时，才回退到历史兼容键。
    if value_flag is None:

        # 旧调用方没有迁到新键时，这里继续接住历史兼容开关。
        value_flag = dict_options.get(STR_LEGACY_ABI_WRAPPER_KEY, False)  # 兼容旧调用方仍在传递的历史 ABI wrapper 开关

    # 返回布尔化后的 ABI wrapper 开关，给下游门禁直接使用。
    return bool(value_flag)

# _gate_kwargs 汇总质量门族共用的关键字参数，并补上 ABI wrapper 兼容键。
def _gate_kwargs(
    *,
    strict: bool,
    comment_language: str,
    formatter_profile: str,
    include_testbench: bool,
    abi_wrapper: bool,
) -> dict[str, Any]:
    """构造传给质量门族函数的稳定关键字参数字典。

    :param strict: 当前门禁是否按严格模式阻断。
    :param comment_language: 门禁检查使用的注释语种。
    :param formatter_profile: 当前门禁选择的 formatter 规则档位。
    :param include_testbench: 是否把 testbench 一并纳入检查。
    :param abi_wrapper: 是否启用 ABI wrapper 兼容模式。
    :return: 返回可直接展开给质量门族函数的关键字参数字典。
    """

    # 向下游函数传参时仍保留历史兼容键名，但不在源码中直写旧术语。
    dict_gate_kwargs = {  # 质量门族共享的基础关键字参数字典
        "strict": strict,  # 当前门禁是否按严格模式执行
        "comment_language": comment_language,  # 当前门禁要求的注释语种
        "formatter_profile": formatter_profile,  # 当前门禁使用的 formatter 档位
        "include_testbench": include_testbench,  # 当前门禁是否纳入 testbench
    }

    # 历史兼容键只在下游调用字典中保留，不向 facade 外部再扩散。
    dict_gate_kwargs[STR_LEGACY_ABI_WRAPPER_KEY] = abi_wrapper  # 下游门禁仍识别的 ABI wrapper 兼容键

    # 返回完整关键字参数字典，供质量门和交付门共用。
    return dict_gate_kwargs

# check_verilog_quality 提供对质量门的稳定 facade 包装。
def check_verilog_quality(
    artifacts_path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """对 Verilog 工件运行 formatter-AST 质量门。

    参数:
        artifacts_path: 待检查工件目录或目标 RTL 文件路径。
        config: 新式 facade 配置字典；缺省时只使用 legacy_options。
        legacy_options: 旧调用方传入的兼容关键字参数。

    返回:
        返回 runtime 质量门报告对象的字典化结果。
    """

    # 质量门入口只允许已知兼容键进入 facade。
    set_allowed_keys = _gate_option_keys()  # 质量门入口允许的兼容配置键集合

    # 合并 config 与 legacy_options，得到质量门入口的统一配置视图。
    dict_options = _merged_option_dict("check_verilog_quality", config, legacy_options, allowed_keys=set_allowed_keys)  # 归一化后的质量门配置字典

    # formatter profile 沿用 runtime 的 normalize 缺省值。
    str_formatter_profile = str(dict_options.get("formatter_profile", "formatter-normalize"))  # 质量门使用的 formatter profile 名称

    # 质量门执行前先把路径和布尔开关归一化成稳定局部变量。
    path_artifacts = Path(artifacts_path)  # 当前质量门要检查的工件路径

    # strict 模式决定发现项是否阻断当前门禁入口。
    bool_strict = bool(dict_options.get("strict", True))  # 当前质量门是否按严格模式运行

    # comment_language 告诉 runtime 期望哪种注释语言。
    str_comment_language = str(dict_options.get("comment_language", "zh"))  # 当前质量门期望的注释语言

    # include_testbench 控制 scaffold testbench 是否一起纳入门禁。
    bool_include_testbench = bool(dict_options.get("include_testbench", False))  # 当前质量门是否纳入 testbench

    # ABI wrapper 开关统一兼容新旧配置键。
    bool_abi_wrapper = _abi_wrapper_enabled(dict_options)  # 当前质量门是否启用 ABI wrapper 模式

    # 质量门关键字参数集中构造，便于共享 ABI wrapper 兼容逻辑。
    dict_gate_kwargs = _gate_kwargs(  # 质量门共享关键字参数字典
        strict=bool_strict,  # 让下游 gate 继承 facade 当前的阻断策略
        comment_language=str_comment_language,  # 把本次检查要求的注释语种同步给下游
        formatter_profile=str_formatter_profile,  # 复用调用方当前选择的 formatter 规则档位
        include_testbench=bool_include_testbench,  # 决定本次质量门是否把 testbench 一并审查
        abi_wrapper=bool_abi_wrapper,  # 告诉下游是否保留 ABI 兼容端口豁免
    )

    # 执行 runtime 质量门并收集结构化报告对象。
    report = run_verilog_quality_gate(  # 质量门运行结果对象
        path_artifacts,  # 本次要检查的 RTL 或工件目录
        **dict_gate_kwargs,  # 共享门禁参数与 ABI wrapper 兼容配置
    )

    # JSON 报告主要服务自动化消费，因此单独归一化其路径。
    path_report_json = _optional_path(dict_options.get("report_json"))  # 写给脚本或 CI 的 JSON 报告文件

    # Markdown 报告主要服务人工审阅，因此单独归一化其路径。
    path_report_markdown = _optional_path(dict_options.get("report_md"))  # 给人工检查结果的 Markdown 报告文件

    # 报告写出保持 runtime 既有格式，便于测试和文档复用。
    write_quality_gate_report(
        report,
        json_path=path_report_json,
        markdown_path=path_report_markdown,
    )

    # facade 返回字典化结果，避免上层依赖 runtime 对象类型。
    return report.to_dict()

# check_verilog_deliverable 提供对最终交付门禁的稳定 facade 包装。
def check_verilog_deliverable(
    artifacts_path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """对 Verilog 工件运行最终交付门禁。

    参数:
        artifacts_path: 待检查工件目录或目标 RTL 文件路径。
        config: 新式 facade 配置字典；缺省时只使用 legacy_options。
        legacy_options: 旧调用方传入的兼容关键字参数。

    返回:
        返回 deliverable gate 生成的结构化报告字典。
    """

    # 交付门入口与质量门共享同一组兼容配置键。
    set_allowed_keys = _gate_option_keys()  # 交付门入口允许向下游透传的字段集合

    # 合并 config 与 legacy_options，得到交付门入口的统一配置视图。
    dict_options = _merged_option_dict(  # 归一化后的交付门配置字典
        "check_verilog_deliverable",  # 当前交付门 facade 入口名称
        config,  # 新式调用方传入的配置对象
        legacy_options,  # 旧式关键字参数载荷
        allowed_keys=set_allowed_keys,  # 允许透传给交付门的字段集合
    )

    # 如果调用方没有指定档位，交付门继续沿用 runtime 的 normalize 配置。
    str_formatter_profile = str(dict_options.get("formatter_profile", "formatter-normalize"))  # 交付审查实际采用的 formatter profile 名称

    # 交付门执行前先把路径和布尔开关归一化成稳定局部变量。
    path_artifacts = Path(artifacts_path)  # 当前交付门要检查的工件路径

    # strict 模式决定交付门是否把发现项视为阻断。
    bool_strict = bool(dict_options.get("strict", True))  # 当前交付门是否按严格模式运行

    # comment_language 告诉 deliverable gate 交付审查要遵守哪种注释语种。
    str_comment_language = str(dict_options.get("comment_language", "zh"))  # 交付审查阶段要求的注释语种

    # include_testbench 决定交付审查是否覆盖 scaffold testbench。
    bool_include_testbench = bool(dict_options.get("include_testbench", False))  # 当前交付门是否纳入 testbench

    # 交付门阶段也统一复用新旧 ABI wrapper 键解析逻辑。
    bool_abi_wrapper = _abi_wrapper_enabled(dict_options)  # 当前交付门是否启用 ABI wrapper 模式

    # 可选规格支持字典、JSON 文本或 JSON 路径三种 facade 输入。
    dict_spec = _load_optional_json(dict_options.get("spec"))  # PG 门禁使用的归一化规格

    # 交付门共享关键字参数时沿用质量门那一套 ABI wrapper 兼容规则。
    dict_gate_kwargs = _gate_kwargs(  # 交付门共享关键字参数字典
        strict=bool_strict,  # 让最终交付判定沿用 facade 当前的 fail 策略
        comment_language=str_comment_language,  # 把交付审查阶段要求的注释语种同步给下游
        formatter_profile=str_formatter_profile,  # 延续交付审查所选的 formatter 规则档位
        include_testbench=bool_include_testbench,  # 决定交付结论是否覆盖配套 testbench
        abi_wrapper=bool_abi_wrapper,  # 让交付审查与质量门保持同一 ABI 兼容判断
    )

    # 交付门把共享参数整体展开给下游实现，避免这里再重复拼参数。
    dict_report = run_verilog_deliverable_gate(  # 交付门运行结果字典
        path_artifacts,  # 本次要审查的交付工件路径
        spec=dict_spec,  # PG 门禁使用的可选规格合同
        **dict_gate_kwargs,  # 交付门下游实际使用的关键字参数集合
    )

    # JSON 报告主要用于沉淀自动化交付证据。
    path_report_json = _optional_path(dict_options.get("report_json"))  # 归档交付门机器结果的 JSON 文件

    # Markdown 报告主要用于沉淀人工交付结论。
    path_report_markdown = _optional_path(dict_options.get("report_md"))  # 归档交付门人工结论的 Markdown 文件

    # 报告写出保持 deliverable gate 既有格式。
    write_verilog_deliverable_gate_report(
        dict_report,
        json_path=path_report_json,
        markdown_path=path_report_markdown,
    )

    # facade 直接返回稳定字典，避免上传 runtime 内部类型。
    return dict_report

# validate_verilog_artifacts 提供生成后工件验证与报告写出入口。
def validate_verilog_artifacts(
    spec: JsonSource,
    artifacts_path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """验证已生成的 Verilog 工件，并可选写出验证报告 JSON。

    参数:
        spec: 原始规范来源，可以是路径、字典或 JSON 文本。
        artifacts_path: 待验证工件目录或 RTL 文件路径。
        config: 新式 facade 配置字典；缺省时只使用 legacy_options。
        legacy_options: 旧调用方传入的兼容关键字参数。

    返回:
        返回 validate_generated 报告对象的字典化结果。
    """

    # 工件验证白名单文本先单独命名，避免集合定义行过长。
    str_allowed_keys_text = (
        "target design_requirements pipeline_required streamability "
        "interface_family interface_profile run_external external_target "
        "readiness comment_language semantic_contract report_json"
    )  # 工件验证入口允许的兼容键文本

    # 这里把键文本切成集合，后面只允许这些字段进入工件验证 facade。
    set_allowed_keys = set(str_allowed_keys_text.split())  # 工件验证入口允许透传给 facade 的字段集合

    # 合并 config 与 legacy_options，得到工件验证入口的统一配置视图。
    dict_options = _merged_option_dict(  # 工件验证入口合并后的配置字典
        "validate_verilog_artifacts",  # 当前 facade 入口名称
        config,  # 新式配置字典
        legacy_options,  # 旧式关键字参数
        allowed_keys=set_allowed_keys,  # 允许的兼容字段集合
    )

    # 外部目标文本需要先独立归一化，供 readiness 判定和 runtime 调用复用。
    str_external_target = str(dict_options.get("external_target", "remote"))  # 当前验证请求指向的外部目标环境

    # readiness 文本既决定外部执行开关，也进入最终验证报告。
    str_readiness = str(dict_options.get("readiness", "static"))  # 当前验证准备度文本

    # 注释语言文本会直接传入 runtime 验证入口。
    str_comment_language = str(dict_options.get("comment_language", "zh"))  # 当前验证期望的注释语言

    # target 字段先经过 facade 兼容解析，避免 runtime 再兜底。
    str_target = _resolve_target(dict_options.get("target"), spec, {})  # facade 解析出的验证目标类型

    # design requirements 可能来自路径、JSON 文本或已加载字典。
    dict_design_requirements = _load_optional_json(dict_options.get("design_requirements"))  # 归一化后的设计约束字典

    # interface profile 需要在进入 runtime 前先统一成字典或空值。
    dict_interface_profile = _load_optional_json(dict_options.get("interface_profile"))  # 归一化后的接口配置字典

    # semantic contract 会作为最终验证阶段的比对合同输入。
    dict_semantic_contract = _load_optional_json(dict_options.get("semantic_contract"))  # 归一化后的参考合同字典

    # 先把调用方的 run_external 意图归一化成布尔值。
    bool_requested_external = bool(dict_options.get("run_external", True))  # 调用方是否请求外部验证

    # run_external 由调用方意图、readiness 和目标环境共同决定。
    bool_run_external = _resolve_external_run(  # 当前验证是否执行外部链路
        bool_requested_external,  # 调用方给出的外部验证意图
        readiness=str_readiness,  # 当前验证准备度
        external_target=str_external_target,  # 目标外部执行环境
    )

    # pipeline_required 代表是否强制要求流水线语义。
    value_pipeline_required = dict_options.get("pipeline_required")  # 原样透传的流水线约束值

    # streamability 代表是否要求设计支持流式处理。
    value_streamability = dict_options.get("streamability")  # 原样透传的流式化约束值

    # interface_family 代表调用方限定的接口族。
    value_interface_family = dict_options.get("interface_family")  # 原样透传的接口族约束值

    # 解析后的规范字典补齐 target、接口和设计约束字段。
    dict_resolved_spec = _prepare_facade_spec(  # 验证前补齐关键约束字段的规范字典
        spec,  # 原始规范来源
        target=str_target,  # facade 已推断出的目标形态
        design_requirements=dict_design_requirements,  # 设计约束补充信息
        # 这一组参数描述设计对数据通路语义的额外约束。
        pipeline_required=value_pipeline_required,  # 是否必须满足流水线要求
        streamability=value_streamability,  # 是否要求支持流式数据通路
        # 这一组参数描述接口层面的额外合同细节。
        interface_family=value_interface_family,  # 调用方限定的接口族
        interface_profile=dict_interface_profile,  # 接口细节补充配置
    )

    # 验证阶段只处理传入的工件路径，不在 runtime 内重复解析字符串路径。
    path_artifacts = Path(artifacts_path)  # 待验证工件的标准化路径

    # 执行 runtime 生成物验证并拿到结构化报告对象。
    report = validate_generated(  # 工件验证报告对象
        dict_resolved_spec,  # 已补齐验证约束的规范字典
        path_artifacts,  # 待验证工件的实际落盘路径
        target="rtl",  # 这里固定检查 RTL 产物而非其他目标
        run_external=bool_run_external,  # 是否继续进入外部验证链路
        readiness=str_readiness,  # 当前请求的验证准备级别
        comment_language=str_comment_language,  # 当前检查要求的注释语种
        semantic_contract=dict_semantic_contract,  # 对照用的参考合同载荷
    )

    # facade 对外统一返回字典，避免泄露 runtime 内部对象。
    payload = report.to_dict()  # 验证报告字典

    # 仅在上层显式请求时写出 JSON 报告文件。
    if dict_options.get("report_json") is not None:

        # 报告路径只在显式请求 JSON 落盘时才做 Path 归一化。
        path_out = _optional_path(dict_options.get("report_json"))  # JSON 报告文件路径

        # 先确保父目录存在，避免报告写出因目录缺失失败。
        path_out.parent.mkdir(parents=True, exist_ok=True)

        # 把验证报告稳定写成 UTF-8 JSON，便于测试和归档。
        path_out.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    # 返回字典化报告，供 facade 调用方继续消费。
    return payload
