"""Verilog facade 的 workflow、prompt 与规格辅助逻辑。"""

# future annotations 避免注解在导入期求值。
from __future__ import annotations

# json 用于读取和落盘 workflow 相关的结构化文件。
import json

# deepcopy 用于保护调用方传入的内存态 spec 字典。
from copy import deepcopy

# Path 负责统一 spec、run_dir 与工件路径。
from pathlib import Path

# Any 用于兼容旧 facade 的自由配置字典。
from typing import Any

# settings runtime 负责读取默认 workflow 配置。
from scripts.python.workflow.config import load_settings, workflow_defaults

# prompt runtime 负责渲染最终 Verilog 生成提示词。
from scripts.python.workflow.prompt import render_prompt

# requirements runtime 负责补齐 RTL 规格中的需求确认与 codegen 计划。
from scripts.python.workflow.requirements import (
    RequirementConfirmation,
    apply_requirement_defaults,
    build_codegen_plan,
    build_requirements_payload,
    validate_requirement_confirmation,
)

# spec runtime 负责规范化与读写 workflow 使用的 spec 文件。
from scripts.python.workflow.spec import normalize_spec, read_spec, write_spec

# validation runtime 提供 readiness 档位比较能力。
from scripts.python.validation.validation import readiness_at_least

# workflow runtime 负责 staged workflow 的实际运行。
from scripts.python.workflow.workflow import run_workflow

# workspace runtime 负责把相对路径绑定到指定 run_dir。
from scripts.python.workflow.workspace import use_workspace_root

# JsonSource 表示 facade 接受的 JSON 风格输入来源。
JsonSource = str | Path | dict[str, Any]  # spec、evidence、decision 等 JSON 风格输入来源

# PathCollectionSource 表示 facade 接受的单路径或多路径集合来源。
PathCollectionSource = str | Path | list[str | Path]  # host-facing facade 暴露的路径集合输入类型

# workflow runtime 结果需要固定字段顺序，便于测试与报告做稳定对比。
WORKFLOW_RUNTIME_OPTION_KEYS = tuple(  # workflow runtime 参数的稳定字段顺序
    "target readiness max_attempts stop_on_human run_external "
    "comment_language provider_name model_timeout_s generation_mode stream".split()
)

# load_default_workflow_config 提供默认 workflow 配置读取入口。
def load_default_workflow_config() -> dict[str, Any]:
    """读取随包默认 workflow 配置。

    参数:
        无额外业务参数；函数直接读取 runtime settings。

    返回:
        返回 workflow 默认配置的字典副本。
    """

    # 直接返回 runtime 提供的 workflow 默认配置字典。
    return workflow_defaults(load_settings())

# load_workflow_result 负责读取已有 run 目录里的 workflow_result.json。
def load_workflow_result(run_dir: str | Path) -> dict[str, Any]:
    """读取已有 workflow 运行目录中的 workflow_result.json。

    参数:
        run_dir: 已有 workflow 运行目录路径。

    返回:
        返回 workflow_result.json 的字典化内容。
    """

    # workflow_result.json 路径在这里基于 run_dir 一次性展开。
    path_result = Path(run_dir) / "workflow_result.json"  # 当前 run_dir 对应的 workflow_result.json 路径

    # 返回 workflow_result.json 的字典化内容。
    return json.loads(path_result.read_text(encoding="utf-8"))

# _option_value 按显式参数、默认配置和兜底值顺序解析单项配置。
def _option_value(
    dict_options: dict[str, Any],
    str_option_key: str,
    dict_defaults: dict[str, Any],
    default_value: Any,
) -> Any:
    """按显式参数、默认配置和兜底值顺序解析单项配置。

    参数:
        dict_options: 调用方已经归一化好的配置字典。
        str_option_key: 当前要解析的配置键名。
        dict_defaults: runtime 提供的默认配置字典。
        default_value: 当显式参数和默认配置都缺失时使用的兜底值。

    返回:
        返回当前配置键最终应该采用的值。
    """

    # 显式传入的非空值优先级最高，直接覆盖默认配置。
    if dict_options.get(str_option_key) is not None:

        # 返回调用方显式提供的配置值。
        return dict_options[str_option_key]

    # 返回默认配置或硬编码兜底值。
    return dict_defaults.get(str_option_key, default_value)

# _optional_path 把可选路径输入统一转换成 Path 或 None。
def _optional_path(value: str | Path | None) -> Path | None:
    """把可选路径输入统一转换成 Path 或 None。

    参数:
        value: 可能为空的路径输入。

    返回:
        如果 value 为空则返回 None，否则返回对应的 Path 对象。
    """

    # 空值要保留为 None，避免误造出当前目录 Path。
    if value is None:

        # 返回空值语义，供上层判断是否省略该路径参数。
        return None

    # 返回标准化后的 Path 对象。
    return Path(value)

# _merged_option_dict 合并新式 config 与旧式兼容关键字参数。
def _merged_option_dict(
    option_name: str,
    config: Any | None,
    dict_legacy_options: dict[str, Any],
    *,
    allowed_keys: set[str],
) -> dict[str, Any]:
    """合并新式 config 与旧式兼容关键字参数，并拒绝未知字段。

    参数:
        option_name: 当前 facade 入口名称，用于错误信息展示。
        config: 新式配置对象，可以为 None、字典或具备属性的对象。
        dict_legacy_options: 旧式关键字参数字典。
        allowed_keys: 当前入口允许透传的字段集合。

    返回:
        返回合并后的统一配置字典。

    异常:
        TypeError: 当 legacy_options 中出现不被允许的字段时抛出。
    """

    # 缺省 config 需要先归一化为空字典，便于后续 update。
    if config is None:

        # 空配置输入在这里落成新的可变字典。
        dict_options: dict[str, Any] = {}  # 当前入口合并后的统一配置字典

    # 字典配置可以直接复制，避免原对象被下游修改。
    elif isinstance(config, dict):

        # 复制字典配置，保护调用方传入对象不被原地修改。
        dict_options = config.copy()  # 从字典 config 复制出的统一配置字典

    # 其余对象配置通过 vars() 提取属性字典。
    else:

        # 把对象属性复制成新字典，兼容 argparse namespace 等输入。
        dict_options = vars(config).copy()  # 从对象 config 复制出的统一配置字典

    # 未知 legacy key 会在这里统一收集，便于一次性报错。
    set_unknown_keys = set(dict_legacy_options) - allowed_keys  # 当前入口不允许透传的 legacy 字段集合

    # 只要存在未知字段，就不能继续进入 facade 主流程。
    if set_unknown_keys:

        # 未知字段文本会直接写进错误信息，帮助调用方修正入参。
        str_unknown_keys = ", ".join(sorted(set_unknown_keys))  # 当前入口收到的未知字段文本

        # 直接抛出兼容关键字参数错误，阻止未知字段继续下传。
        raise TypeError(
            "> ERR: [Python] Unexpected keyword arguments: "
            f"{option_name} -> {str_unknown_keys}.",
        )

    # 旧式关键字参数在这里覆盖到统一配置视图上。
    dict_options.update(dict_legacy_options)

    # 返回合并后的统一配置字典。
    return dict_options

# _load_raw_spec 读取原始 spec，并保护调用方传入的内存态字典。
def _load_raw_spec(spec: JsonSource) -> dict[str, Any]:
    """读取原始 spec，并保护调用方传入的内存态字典。

    参数:
        spec: 路径形式或字典形式的原始规格输入。

    返回:
        返回可安全修改的原始 spec 字典副本。
    """

    # 内存态字典在这里深拷贝，避免默认值补齐影响调用方对象。
    if isinstance(spec, dict):

        # 返回与调用方对象解耦的 spec 深拷贝。
        return deepcopy(spec)

    # 路径形式的 spec 在这里统一转成 Path。
    path_spec = Path(spec)  # 当前原始 spec 文件路径

    # 返回从 spec 文件读取并解析得到的字典。
    return json.loads(path_spec.read_text(encoding="utf-8"))

# _load_optional_json 把可选 JSON 输入统一读取成字典。
def _load_optional_json(value: str | Path | dict[str, Any] | None) -> dict[str, Any] | None:
    """把可选 JSON 输入统一读取成字典，同时保留空值语义。

    参数:
        value: 可选 JSON 输入，可以是 None、路径或已加载字典。

    返回:
        如果 value 为空则返回 None，否则返回字典化内容。
    """

    # 空值要保留为 None，供上层明确判断该输入是否缺席。
    if value is None:

        # 返回空值语义，避免误造空字典掩盖缺席状态。
        return None

    # 已经是字典的输入直接复用，不额外落盘或读取文件。
    if isinstance(value, dict):

        # 返回原始字典对象，保持调用方已经准备好的结构。
        return value

    # 路径形式的 JSON 输入在这里统一转成 Path。
    path_value = Path(value)  # 当前可选 JSON 文件路径

    # 最后从 JSON 文件中读取并返回字典化内容。
    return json.loads(path_value.read_text(encoding="utf-8"))

# _resolve_target 把目标类型收敛到 facade 支持的唯一 rtl。
def _resolve_target(
    target: str | None,
    spec: JsonSource | None,
    dict_config: dict[str, Any],
) -> str:
    """把目标类型收敛到 facade 支持的唯一 rtl。

    参数:
        target: 调用方显式提供的 target 值。
        spec: 可选 spec 输入，用于回看 spec 自带的 target 字段。
        dict_config: workflow 默认配置或合并后的配置字典。

    返回:
        返回 facade 允许的唯一目标值 rtl。

    异常:
        ValueError: 当最终解析出的 target 不是 rtl 时抛出。
    """

    # raw target 缺省为空，只有 spec 存在时才继续回看 spec 字段。
    raw_target: Any | None = None  # 从 spec 中回看的原始 target 值

    # spec 存在时要先读取其原始 target 字段，参与目标解析。
    if spec is not None:

        # 从原始 spec 中回看 target 字段，保持旧入口兼容行为。
        raw_target = _load_raw_spec(spec).get("target")  # spec 自带的原始 target 值

    # target 解析顺序保持显式参数、spec、默认配置、最终兜底值不变。
    str_resolved_target = str(target or raw_target or dict_config.get("target") or "rtl").lower()  # facade 解析出的最终目标值

    # 当前 facade 只允许 rtl 目标继续进入主流程。
    if str_resolved_target != "rtl":

        # 直接阻止非 rtl 目标进入 facade 主路径。
        raise ValueError("> ERR: [Python] Only target 'rtl' is supported.")

    # 返回 facade 唯一允许的目标值。
    return "rtl"

# _workflow_overrides 兼容 workflow 配置嵌套在 workflow 字段下的旧结构。
def _workflow_overrides(dict_value: dict[str, Any]) -> dict[str, Any]:
    """兼容 workflow 配置嵌套在 workflow 字段下的旧结构。

    参数:
        dict_value: 已经字典化的 workflow 配置输入。

    返回:
        如果检测到嵌套 workflow 字典则返回该子字典，否则返回原字典。
    """

    # 嵌套 workflow 字段在这里单独提取，兼容旧配置形状。
    dict_nested_workflow = dict_value.get("workflow")  # 旧配置中嵌套的 workflow 字段

    # 旧结构存在嵌套 workflow 字典时，优先返回该子字典。
    if isinstance(dict_nested_workflow, dict):

        # 返回嵌套 workflow 子字典，保持旧配置兼容行为。
        return dict_nested_workflow

    # 否则直接返回原配置字典。
    return dict_value

# _write_json_object 把字典稳定写成 UTF-8 JSON 文件并返回路径。
def _write_json_object(path_output: Path, dict_payload: dict[str, Any]) -> Path:
    """把字典稳定写成 UTF-8 JSON 文件并返回路径。

    参数:
        path_output: 目标 JSON 文件路径。
        dict_payload: 待落盘的结构化字典。

    返回:
        返回已经写出的 JSON 文件路径。
    """

    # 父目录在写文件前必须存在，避免 JSON 落盘失败。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 把字典稳定写成 UTF-8 JSON 文本，并保留换行结尾。
    path_output.write_text(
        json.dumps(dict_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 返回已经写出的 JSON 文件路径。
    return path_output

# _materialize_optional_json 把可选 JSON 输入统一收敛成 runtime 可读取的路径。
def _materialize_optional_json(
    value: str | Path | dict[str, Any] | None,
    path_output: Path,
) -> Path | None:
    """把可选 JSON 输入统一收敛成 runtime 可读取的路径。

    参数:
        value: 可选 JSON 输入，可以是 None、路径或已加载字典。
        path_output: 当 value 是字典时用于承接落盘结果的目标路径。

    返回:
        如果 value 为空则返回 None，否则返回 runtime 可读取的路径。
    """

    # 空值要保留为 None，避免生成伪造的空文件路径。
    if value is None:

        # 返回空值语义，表示当前 JSON 输入没有提供。
        return None

    # 已经是路径的输入直接转成 Path，避免重复落盘。
    if isinstance(value, (str, Path)):

        # 返回 runtime 可直接读取的现有路径。
        return Path(value)

    # 字典输入需要先准备父目录，再落盘成 JSON 文件。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 把内存态 JSON 字典稳定写成 runtime 可读取的文件。
    path_output.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    # 返回内存态 JSON 字典落盘后的目标路径。
    return path_output

# _prepare_facade_spec 准备 workflow 与 prompt 共用的标准化 RTL 规格。
def _prepare_facade_spec(
    spec: JsonSource,
    *,
    target: str | None,
    design_requirements: dict[str, Any] | None, pipeline_required: bool | None,
    streamability: str | None, interface_family: str | None,
    interface_profile: dict[str, Any] | None,
) -> dict[str, Any]:
    """准备 workflow 与 prompt 共用的标准化 RTL 规格。

    参数:
        spec: 原始 spec 输入，可以是路径或内存态字典。
        target: 调用方显式提供的目标类型。
        design_requirements: 可选设计约束字典。
        pipeline_required: 可选流水线约束。
        streamability: 可选流式化约束。
        interface_family: 可选接口族约束。
        interface_profile: 可选接口细节约束字典。

    返回:
        返回补齐默认值并通过验证的标准化 RTL 规格字典。

    异常:
        ValueError: 当 rtl_dialect 不是 Verilog-2001 兼容值时抛出。
    """

    # 先复制原始 spec，避免默认值补齐回写到调用方对象。
    dict_raw_spec = _load_raw_spec(spec)  # 当前调用使用的原始 spec 副本

    # facade 入口先把 target 收敛到唯一允许的 rtl。
    _resolve_target(target, dict_raw_spec, {})

    # dialect 不兼容时要尽早阻断，避免错误规格继续下传。
    if dict_raw_spec.get("rtl_dialect") not in (None, "", "verilog"):

        # 这里只接受 Verilog-2001 兼容的方言标识。
        raise ValueError("> ERR: [Python] Only Verilog-2001 is supported.")

    # 统一写回 facade 固定支持的 target。
    dict_raw_spec["target"] = "rtl"  # facade 固定支持的目标类型

    # 同时锁定 runtime 侧消费的 RTL 方言。
    dict_raw_spec["rtl_dialect"] = "verilog"  # facade 固定支持的 RTL 方言

    # 只要显式给出任一需求覆盖，就视为用户已经确认方向。
    tuple_confirmation_overrides = (  # 用来判断用户是否显式给出过确认信号的覆盖项
        design_requirements,  # 当前调用显式给出的设计约束覆盖
        pipeline_required,  # 当前调用显式给出的流水线约束覆盖
        streamability,  # 当前调用显式给出的流式化约束覆盖
        interface_family,  # 当前调用显式给出的接口族约束覆盖
        interface_profile,  # 当前调用显式给出的接口细节约束覆盖
    )

    # 任一覆盖项非空时，都视为用户已经确认需求方向。
    bool_user_confirmed = any(item is not None for item in tuple_confirmation_overrides)  # 当前调用是否收到显式需求确认信号

    # 把确认状态单独组装给 defaults 层复用。
    requirement_confirmation_state = RequirementConfirmation(confirmed_by_user=True if bool_user_confirmed else None)  # apply_requirement_defaults 需要的确认状态

    # 这里补齐需求确认、接口约束和设计约束默认值。
    dict_enriched_spec = apply_requirement_defaults(  # 补齐需求确认、接口与设计约束默认值
        dict_raw_spec,  # 作为 defaults 输入的基础 spec
        design_requirements=design_requirements,  # 透传显式设计约束
        pipeline_required=pipeline_required,  # 透传显式流水线约束
        streamability=streamability,  # 透传显式流式化约束
        interface_family=interface_family,  # 透传显式接口族约束
        interface_profile=interface_profile, confirmation=requirement_confirmation_state,  # 透传接口细节约束和确认状态
    )

    # defaults 处理后的 spec 还要再收敛成 runtime 约定形状。
    dict_normalized_spec = normalize_spec(dict_enriched_spec, target="rtl")  # 当前调用可直接交给 runtime 的规范化 spec

    # 最后执行需求确认校验，确保约束载荷完整。
    validate_requirement_confirmation(dict_normalized_spec)

    # 返回补齐默认值并通过校验的标准化 RTL spec。
    return dict_normalized_spec

# _materialize_spec 把路径或字典形式的 spec 物化成 workflow 可读取的文件。
def _materialize_spec(
    spec: JsonSource,
    path_output: Path,
    *,
    target: str | None,
) -> Path:
    """把路径或字典形式的 spec 物化成 workflow 可读取的文件。

    参数:
        spec: 路径形式或字典形式的 spec 输入。
        path_output: 规范化 spec 的目标落盘路径。
        target: 当前调用已经解析出的目标类型。

    返回:
        返回落盘后的 spec 文件路径。
    """

    # 路径形式的 spec 在这里交给 runtime 读并规范化。
    if isinstance(spec, (str, Path)):

        # 从现有 spec 文件读取并规范化得到 runtime 可消费的字典。
        dict_normalized_spec = read_spec(Path(spec), target=target)  # 从文件读取并规范化后的 spec 字典

    # 内存态 spec 在这里直接进入 normalize_spec。
    else:

        # 从内存态字典规范化得到 runtime 可消费的 spec 字典。
        dict_normalized_spec = normalize_spec(spec, target=target)  # 从字典规范化后的 spec 字典

    # spec 输出父目录在写文件前必须存在。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 把规范化 spec 稳定写成 workflow 后续阶段可读取的文件。
    write_spec(path_output, dict_normalized_spec)

    # 返回规范化 spec 的落盘路径。
    return path_output

# _resolve_external_run 根据 readiness 与目标策略裁决是否执行外部验证。
def _resolve_external_run(
    run_external: bool,
    *,
    readiness: str,
    external_target: str,
    allow_static_external: bool = False,
) -> bool:
    """根据 readiness 与目标策略裁决是否执行外部验证。

    参数:
        run_external: 调用方是否请求外部验证。
        readiness: 当前验证准备度文本。
        external_target: 调用方请求的外部目标环境标识。
        allow_static_external: 是否允许 static 档位继续执行外部验证。

    返回:
        返回当前调用最终是否应该执行外部验证。

    异常:
        ValueError: 当外部验证目标不是 local 且策略不允许继续时抛出。
    """

    # 调用方没有请求外部验证时，直接关闭该链路。
    if not run_external:

        # 返回 False，表示当前调用不进入外部验证。
        return False

    # static 以下的准备度在默认策略下不能继续进入外部验证。
    if not allow_static_external and not readiness_at_least(readiness, "compile"):

        # 返回 False，表示当前准备度不足以执行外部验证。
        return False

    # 当前 facade 的本地外部验证必须显式落到 local 目标。
    if external_target != "local":

        # 阻止 remote-first 情况被错误地当成本地外部验证执行。
        raise ValueError(
            "> ERR: [Python] External validation is remote-first. Use the remote "
            "validation flow, or pass external_target='local' only after the user "
            "explicitly approves local external validation."
        )

    # 返回 True，表示当前调用允许执行外部验证。
    return True

# _workflow_runtime_payload 按固定字段顺序返回 workflow runtime 参数字典。
def _workflow_runtime_payload(dict_runtime_values: dict[str, Any]) -> dict[str, Any]:
    """按固定字段顺序返回 workflow runtime 参数字典。

    参数:
        dict_runtime_values: 已经解析完成的 runtime 参数字典。

    返回:
        返回字段顺序稳定的 workflow runtime 参数字典。
    """

    # 最终按固定字段顺序回放 runtime 参数字典。
    return {str_key: dict_runtime_values[str_key] for str_key in WORKFLOW_RUNTIME_OPTION_KEYS}

# _resolved_workflow_runtime_options 解析 workflow 运行时最终采用的参数。
def _resolved_workflow_runtime_options(
    spec: JsonSource | None,
    dict_options: dict[str, Any],
) -> dict[str, Any]:
    """解析 workflow 运行时最终采用的参数。

    参数:
        spec: 当前 workflow 调用收到的 spec 输入，可以为空。
        dict_options: 当前 workflow 入口合并后的统一配置字典。

    返回:
        返回字段顺序稳定的 workflow runtime 参数字典。
    """

    # 先读取仓库默认 workflow 配置，后面逐项做回退。
    dict_defaults = load_default_workflow_config()  # runtime 提供的 workflow 默认配置字典

    # 再展开调用方传来的 workflow_config 覆盖。
    dict_workflow_payload = _load_optional_json(dict_options.get("workflow_config")) or {}  # 当前调用附带的 workflow 配置覆盖字典

    # 如果还在使用旧结构，这里抽出嵌套 workflow 段。
    dict_overrides = _workflow_overrides(dict_workflow_payload)  # 当前调用实际生效的 workflow 覆盖字典

    # 默认值与覆盖值在这里拼成同一张视图。
    dict_merged = {**dict_defaults, **dict_overrides}  # workflow 默认配置与覆盖配置合并后的统一视图

    # 目标类型必须先收敛到 facade 唯一支持的 rtl。
    str_resolved_target = _resolve_target(dict_options.get("target"), spec, dict_merged)  # 当前 workflow 最终采用的 target

    # readiness 档位缺省时回退到 static。
    str_resolved_readiness = str(_option_value(dict_options, "readiness", dict_merged, "static"))  # 当前 workflow 真正执行的 readiness 档位

    # 最大尝试次数在这里收敛成整数。
    int_resolved_attempts = int(_option_value(dict_options, "max_attempts", dict_merged, 3))  # 当前 workflow 最终采用的最大尝试次数

    # stop_on_human 决定需要人工时是否暂停链路。
    bool_resolved_stop_on_human = bool(_option_value(dict_options, "stop_on_human", dict_merged, True))  # 当前 workflow 是否在需要人工时暂停

    # 先单独解析调用方的外部验证意图。
    bool_requested_run_external = bool(_option_value(dict_options, "run_external", dict_merged, True))  # 当前 workflow 是否收到外部验证请求

    # 再读取本次请求指向的外部目标环境。
    str_resolved_external_target = str(dict_options.get("external_target", "remote"))  # 当前 workflow 请求的外部目标环境

    # readiness 与目标策略会共同裁决最终是否跑外部验证。
    bool_resolved_run_external = _resolve_external_run(  # readiness 与目标策略共同裁决后的外部验证开关
        bool_requested_run_external,  # 调用方是否发出了外部验证请求
        readiness=str_resolved_readiness,  # 裁决时使用的 readiness 档位
        external_target=str_resolved_external_target,  # 裁决时使用的外部目标环境
    )

    # 注释语种沿用 workflow 默认值或本次覆盖值。
    str_resolved_comment_language = str(_option_value(dict_options, "comment_language", dict_merged, "zh"))  # 当前 workflow 最终采用的注释语种

    # provider 名称会影响后续模型调用后端。
    str_resolved_provider_name = str(_option_value(dict_options, "provider_name", dict_merged, "command"))  # 当前 workflow 最终采用的 provider 名称

    # 模型超时统一换算成秒级整数。
    int_resolved_timeout = int(_option_value(dict_options, "model_timeout_s", dict_merged, 120))  # 当前 workflow 最终采用的模型超时时间

    # generation_mode 决定 workflow 的执行形态。
    str_resolved_generation_mode = str(_option_value(dict_options, "generation_mode", dict_merged, "regular"))  # 当前 workflow 最终采用的生成模式

    # stream 开关单独保留给 provider 层使用。
    bool_resolved_stream = bool(_option_value(dict_options, "stream", dict_merged, False))  # 当前 workflow 是否启用流式输出

    # 先准备一个可按固定顺序填充的 runtime 参数视图。
    dict_runtime_values: dict[str, Any] = {}  # run_workflow 最终消费的参数视图

    # 目标类型写入最终 runtime 参数视图。
    dict_runtime_values["target"] = str_resolved_target  # 目标类型

    # readiness 档位写入最终 runtime 参数视图。
    dict_runtime_values["readiness"] = str_resolved_readiness  # 准备度档位

    # 最大尝试次数写入最终 runtime 参数视图。
    dict_runtime_values["max_attempts"] = int_resolved_attempts  # 最大尝试次数

    # stop_on_human 写入最终 runtime 参数视图。
    dict_runtime_values["stop_on_human"] = bool_resolved_stop_on_human  # 人工确认暂停开关

    # run_external 这一项记录的是裁决后的最终执行开关。
    dict_runtime_values["run_external"] = bool_resolved_run_external  # 外部验证执行开关

    # 注释语种写入最终 runtime 参数视图。
    dict_runtime_values["comment_language"] = str_resolved_comment_language  # 注释语种

    # provider 名称写入最终 runtime 参数视图。
    dict_runtime_values["provider_name"] = str_resolved_provider_name  # 生成 provider 名称

    # 模型超时写入最终 runtime 参数视图。
    dict_runtime_values["model_timeout_s"] = int_resolved_timeout  # 模型超时秒数

    # 生成模式写入最终 runtime 参数视图。
    dict_runtime_values["generation_mode"] = str_resolved_generation_mode  # 生成模式

    # 流式输出开关写入最终 runtime 参数视图。
    dict_runtime_values["stream"] = bool_resolved_stream  # 流式输出开关

    # 最后按固定字段顺序导出 runtime 参数字典。
    return _workflow_runtime_payload(dict_runtime_values)

# _resume_workflow_run 恢复已有 workflow 目录并返回稳定 facade 结果。
def _resume_workflow_run(
    dict_options: dict[str, Any],
    dict_runtime_options: dict[str, Any],
) -> dict[str, Any]:
    """恢复已有 workflow 目录并返回稳定 facade 结果。

    参数:
        dict_options: 当前 workflow 入口合并后的统一配置字典。
        dict_runtime_options: 当前 workflow 最终采用的 runtime 参数字典。

    返回:
        返回恢复运行后的稳定 facade 结果字典。
    """

    # 先把恢复目标目录标准化成 Path。
    path_run_dir = Path(dict_options["resume_dir"])  # 当前要恢复的 workflow 运行目录

    # 恢复态如果带了 decision，这里统一物化成 runtime 可读取的文件。
    path_decision = _materialize_optional_json(  # 恢复态需要复用的 decision 文件路径
        dict_options.get("decision"),  # 调用方显式给出的 decision 输入
        path_run_dir / "_adapter_inputs" / "decision.json",  # 恢复态 decision 文件的落盘位置
    )

    # 恢复运行时要把 workspace 根切到既有 run_dir。
    with use_workspace_root(path_run_dir):

        # runtime 在这里继续推进已有 workflow 目录。
        dict_workflow_result = run_workflow(  # runtime 返回的恢复执行结果
            resume_dir=path_run_dir,  # 已存在的 workflow 运行目录
            decision_path=path_decision,  # 恢复态继续复用的 decision 文件
            generation_mode=dict_options.get("generation_mode"),  # 恢复态沿用的生成模式
            stream=dict_options.get("stream"),  # 恢复态沿用的流式输出开关
            stop_on_human=dict_runtime_options["stop_on_human"],  # 人工确认时是否暂停流程
            run_external=dict_runtime_options["run_external"],  # 恢复态是否继续执行外部验证
            comment_language=dict_runtime_options["comment_language"],  # 恢复态继续使用的注释语种
            model_timeout_s=dict_runtime_options["model_timeout_s"],  # 恢复态继续使用的模型超时
        )

    # 恢复态同样只对外 surfaced 已解析过的最终 artifact 目录。
    path_artifact_dir = _resolved_latest_artifact_dir(  # 恢复态最终 surfaced artifact 根目录
        path_run_dir,  # 当前 workflow 运行根目录
        dict_workflow_result,  # runtime 返回的完整 workflow 结果字典
    )

    # 对外只返回 facade 约定的稳定恢复结果结构。
    return {
        "status": dict_workflow_result["status"],
        "run_dir": str(path_run_dir),
        "artifact_dir": path_artifact_dir.as_posix() if path_artifact_dir is not None else None,
        "result_path": str(path_run_dir / "workflow_result.json"),
        "workflow_result": dict_workflow_result,
    }

# _new_workflow_result_payload 组装新运行 facade 的稳定结果字典。
def _new_workflow_result_payload(
    dict_workflow_result: dict[str, Any],
    path_run_dir: Path,
    path_requirements: Path,
    path_codegen_plan: Path,
    dict_requirements_payload: dict[str, Any],
    dict_codegen_plan_payload: dict[str, Any],
) -> dict[str, Any]:
    """组装新运行 facade 的稳定结果字典。

    参数:
        dict_workflow_result: runtime 返回的完整 workflow 结果字典。
        path_run_dir: 当前 workflow 运行根目录。
        path_requirements: requirements.json 的落盘路径。
        path_codegen_plan: codegen_plan.json 的落盘路径。
        dict_requirements_payload: requirements.json 对应的内存载荷。
        dict_codegen_plan_payload: codegen_plan.json 对应的内存载荷。

    返回:
        返回新运行 facade 的稳定结果字典。
    """

    # 顶层 facade 结果统一 surfaced 已解析过的最终 artifact 目录。
    path_artifact_dir = _resolved_latest_artifact_dir(  # 新运行最终 surfaced artifact 根目录
        path_run_dir,  # 供 artifact 相对路径回映到本地目录的 run 根
        dict_workflow_result,  # 新运行阶段产出的 attempts 与工件记录
    )

    # 新运行的结果字段在这里统一收口成 facade 合同。
    return {
        "status": dict_workflow_result["status"],
        "run_dir": str(path_run_dir),
        "artifact_dir": path_artifact_dir.as_posix() if path_artifact_dir is not None else None,
        "result_path": str(path_run_dir / "workflow_result.json"),
        "requirements_path": str(path_requirements),
        "codegen_plan_path": str(path_codegen_plan),
        "requirements_payload": dict_requirements_payload,
        "codegen_plan_payload": dict_codegen_plan_payload,
        "workflow_result": dict_workflow_result,
    }

# _start_new_workflow_run 启动新的 spec-first workflow 运行。
def _start_new_workflow_run(
    spec: JsonSource,
    dict_options: dict[str, Any],
    dict_runtime_options: dict[str, Any],
) -> dict[str, Any]:
    """启动新的 spec-first workflow 运行。

    参数:
        spec: 当前 workflow 调用收到的 spec 输入。
        dict_options: 当前 workflow 入口合并后的统一配置字典。
        dict_runtime_options: 当前 workflow 最终采用的 runtime 参数字典。

    返回:
        返回新 workflow 运行的稳定 facade 结果字典。
    """

    # 新 workflow 的根目录先在这里标准化。
    path_run_dir = Path(dict_options["out_dir"])  # 当前新 workflow 的运行根目录

    # 中间输入统一放进 _adapter_inputs，便于和产物目录分层。
    path_inputs_dir = path_run_dir / "_adapter_inputs"  # 当前 workflow 的 adapter 输入目录

    # 写任何中间文件之前先确保 adapter 输入目录已经存在。
    path_inputs_dir.mkdir(parents=True, exist_ok=True)

    # 设计约束先统一读取成字典或空值。
    dict_design_requirements = _load_optional_json(dict_options.get("design_requirements"))  # 当前 workflow 的设计约束字典

    # 接口细节覆盖也在这里做同样的归一化。
    dict_interface_profile = _load_optional_json(dict_options.get("interface_profile"))  # 当前 workflow 的接口细节字典

    # target 已经在 runtime 选项阶段解析完成，这里直接复用。
    str_workflow_target = dict_runtime_options["target"]  # 当前 workflow 已解析出的目标类型

    # pipeline_required 原样保留给 spec 默认值补齐逻辑。
    bool_pipeline_required = dict_options.get("pipeline_required")  # 当前 workflow 是否要求流水线语义

    # streamability 约束同样延后交给 spec 规范化逻辑。
    str_streamability = dict_options.get("streamability")  # 当前 workflow 的流式化约束文本

    # interface_family 约束也要带进 spec 预处理阶段。
    str_interface_family = dict_options.get("interface_family")  # 当前 workflow 的接口族约束文本

    # 先准备 workflow 与 prompt 共用的规范化 spec。
    dict_prepared_spec = _prepare_facade_spec(  # workflow 与 prompt 共用的规范化 spec
        spec,  # 调用方传入的原始 spec 输入
        target=str_workflow_target,  # 锁定本次 workflow 的目标类型
        design_requirements=dict_design_requirements, pipeline_required=bool_pipeline_required,  # 透传设计要求和流水线约束
        streamability=str_streamability, interface_family=str_interface_family,  # 透传流式化约束和接口族约束
        interface_profile=dict_interface_profile,  # 透传接口细节约束
    )

    # requirements.json 的载荷从 prepared spec 中提取。
    dict_requirements_payload = build_requirements_payload(dict_prepared_spec)  # 从 prepared spec 提取出的 requirements 载荷

    # requirements.json 在这里稳定落盘，供后续阶段直接复用。
    path_requirements = _write_json_object(path_inputs_dir / "requirements.json", dict_requirements_payload)  # requirements.json 的落盘路径

    # codegen plan 也从 prepared spec 统一生成。
    dict_codegen_plan = build_codegen_plan(dict_prepared_spec)  # 从 prepared spec 生成的 codegen 计划字典

    # codegen plan 文件单独落盘，便于人工复查生成计划。
    path_codegen_plan = _write_json_object(path_inputs_dir / "codegen_plan.json", dict_codegen_plan)  # 提供人工复查的 codegen 计划文件

    # 先把 plan 路径转成 run_dir 相对文本，保持产物可搬运。
    str_codegen_plan_path = path_codegen_plan.relative_to(path_run_dir).as_posix()  # codegen plan 相对 run_dir 的路径文本

    # 再把相对 plan 路径挂回 prepared spec。
    dict_prepared_spec["codegen_plan_path"] = str_codegen_plan_path  # 供 workflow 后续阶段引用的 codegen plan 相对路径

    # spec 最终要物化成 runtime 可以直接读取的文件。
    path_spec = _materialize_spec(dict_prepared_spec, path_inputs_dir / "spec.json", target=str_workflow_target)  # workflow runtime 最终读取的 spec 文件路径

    # evidence 输入若存在，也在这里转成文件路径。
    path_evidence = _materialize_optional_json(dict_options.get("evidence"), path_inputs_dir / "evidence.json")  # 当前 workflow 复用的 evidence 文件路径

    # decision 输入也要单独落成 adapter 输入文件。
    path_decision = _materialize_optional_json(dict_options.get("decision"), path_inputs_dir / "decision.json")  # 新运行阶段复用的人审决策文件

    # 启动新运行前把 workspace 根绑定到目标 run_dir。
    with use_workspace_root(path_run_dir):

        # runtime 在这里执行新的 staged workflow。
        dict_workflow_result = run_workflow(  # runtime 返回的新执行结果
            spec_path=path_spec,  # 本次运行使用的规范化 spec 文件
            target=str_workflow_target,  # 明确 staged workflow 只生成 RTL 目标
            out_dir=path_run_dir,  # 指向当前 workflow 的运行目录
            decision_path=path_decision,  # 透传本次要复用的 decision 文件
            evidence_path=path_evidence,  # 挂接本次可追溯的验证证据文件
            provider_name=dict_runtime_options["provider_name"],  # 选择本次调用的 provider 名称
            provider_command=dict_options.get("provider_command"),  # 允许调用方覆盖 provider 命令
            generation_mode=dict_runtime_options["generation_mode"],  # 指定本次 workflow 的生成模式
            stream=dict_runtime_options["stream"],  # 控制 provider 是否启用流式输出
            readiness=dict_runtime_options["readiness"],  # 传入当前 workflow 的 readiness 档位
            max_attempts=dict_runtime_options["max_attempts"],  # 限制本次 workflow 的最大尝试次数
            stop_on_human=dict_runtime_options["stop_on_human"],  # 控制人工确认时是否暂停
            run_external=dict_runtime_options["run_external"],  # 控制是否执行外部验证
            comment_language=dict_runtime_options["comment_language"],  # 约束生成注释的语言
            model_timeout_s=dict_runtime_options["model_timeout_s"],  # 约束模型调用的超时秒数
        )

    # 最后回到 facade 的稳定返回结构。
    return _new_workflow_result_payload(
        dict_workflow_result,
        path_run_dir,
        path_requirements,
        path_codegen_plan,
        dict_requirements_payload,
        dict_codegen_plan,
    )

# run_verilog_workflow 提供 staged workflow 的公共 facade 入口。
def run_verilog_workflow(
    spec: JsonSource | None = None,
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """启动或恢复 Verilog workflow，兼容旧关键字参数调用。

    参数:
        spec: 可选 spec 输入；恢复运行时可以为空，新运行时必须提供。
        config: 新式 facade 配置字典；缺省时只使用 legacy_options。
        legacy_options: 旧调用方传入的兼容关键字参数。

    返回:
        返回 workflow 运行或恢复后的稳定 facade 结果字典。

    异常:
        ValueError: 当新运行缺少 spec 或 out_dir 时抛出。
    """

    # workflow 入口允许的字段文本先单独命名，避免集合定义块过密。
    str_workflow_keys_text = (
        "out_dir resume_dir workflow_config evidence decision "
        "provider_name provider_command generation_mode stream target "
        "design_requirements pipeline_required streamability interface_family interface_profile "
        "readiness max_attempts run_external external_target "
        "stop_on_human comment_language model_timeout_s"
    )  # run_verilog_workflow 入口允许的兼容键文本

    # workflow 入口允许的字段集合在这里由文本切分得到。
    set_allowed_keys = set(str_workflow_keys_text.split())  # run_verilog_workflow 入口允许的兼容字段集合

    # config 与 legacy_options 会在这里合并成统一配置字典。
    dict_options = _merged_option_dict("run_verilog_workflow", config, legacy_options, allowed_keys=set_allowed_keys)  # run_verilog_workflow 入口合并后的统一配置字典

    # workflow 运行参数会在这里统一解析成稳定字典。
    dict_runtime_options = _resolved_workflow_runtime_options(spec, dict_options)  # 当前 workflow 最终采用的 runtime 参数字典

    # resume_dir 存在时优先进入恢复路径，而不是新建运行路径。
    if dict_options.get("resume_dir") is not None:

        # 返回恢复已有 run_dir 得到的稳定 facade 结果。
        return _resume_workflow_run(dict_options, dict_runtime_options)

    # 新 workflow 运行必须同时提供 spec 和 out_dir。
    if spec is None or dict_options.get("out_dir") is None:

        # 直接阻止缺失关键输入的新 workflow 运行。
        raise ValueError("> ERR: [Python] New workflow runs require both `spec` and `out_dir`.")

    # 返回新 workflow 运行得到的稳定 facade 结果。
    return _start_new_workflow_run(spec, dict_options, dict_runtime_options)

# _batch_case_id 根据规格输入和序号生成稳定的批量 case 标识。
def _batch_case_id(spec: JsonSource, index: int) -> str:
    """根据规格输入和序号生成稳定的批量 case 标识。

    参数:
        spec: 当前批量 case 对应的 spec 输入。
        index: 当前批量 case 的一基序号。

    返回:
        返回当前批量 case 的稳定标识文本。
    """

    # 内存态 spec 优先复用其 name 字段，保持批量 case 命名可读。
    if isinstance(spec, dict):

        # 返回 name 字段或基于序号生成的兜底 case 标识。
        return str(spec.get("name") or f"case_{index}")

    # 路径形式的 spec 直接复用文件 stem，缺失时退回序号兜底值。
    return Path(spec).stem or f"case_{index}"

# _resolve_result_path 解析 workflow_result 中可能出现的本地路径字段。
def _resolve_result_path(path_run_dir: Path, value: Any) -> Path | None:
    """解析 workflow_result 中可能出现的本地路径字段。

    参数:
        path_run_dir: 当前 workflow run 的根目录。
        value: workflow_result 中读取到的原始路径值。

    返回:
        返回已解析的本地路径；对于无效值或 external 占位路径返回 None。
    """

    # 空值或非字符串值不能被解释成有效的本地路径。
    if not value or not isinstance(value, str):

        # 返回 None，表示当前字段不对应本地路径。
        return None

    # 原始路径文本在这里统一转成 Path。
    path_value = Path(value)  # workflow_result 中读取到的原始路径对象

    # 绝对路径可以直接复用，不需要再拼 run_dir。
    if path_value.is_absolute():

        # 返回原始绝对路径。
        return path_value

    # external 占位路径代表该工件不在本地文件系统下。
    if value.startswith("<external>/"):

        # 返回 None，表示当前字段不对应可访问的本地路径。
        return None

    # 相对路径若在当前工作目录已存在，优先解析成真实绝对路径。
    if path_value.exists():

        # 返回已解析的绝对路径，便于后续直接读取文件。
        return path_value.resolve()

    # 其余相对路径按 run_dir 下的工件路径处理。
    return path_run_dir / path_value

# _resolved_latest_artifact_dir 统一解析 facade 顶层需要 surfaced 的最终 artifact 目录。
def _resolved_latest_artifact_dir(
    path_run_dir: Path,
    dict_workflow_result: dict[str, Any],
) -> Path | None:
    """统一解析 facade 顶层需要 surfaced 的最终 artifact 目录。

    参数:
        path_run_dir: 当前 workflow run 的根目录。
        dict_workflow_result: runtime 返回的完整 workflow 结果字典。

    返回:
        返回已解析的最终 artifact 目录；无法映射到本地路径时返回 None。
    """

    # attempts 只在 workflow_result 是字典时才允许继续读取。
    if not isinstance(dict_workflow_result, dict):

        # 坏形状的 workflow_result 直接视为没有可 surfaced 的本地 artifact。
        return None

    # attempts 列表缺失时回退为空列表，避免越界访问。
    list_attempts = dict_workflow_result.get("attempts") or []  # runtime 记录的所有 workflow attempts

    # 没有 attempt 时，说明当前 workflow 还没有最终 artifact 可以对外 surfaced。
    if not list_attempts:

        # 直接返回 None，保持 facade 顶层结果稳定。
        return None

    # 最后一次 attempt 才代表当前 workflow 对外 surfaced 的最终 artifact。
    dict_latest_attempt = list_attempts[-1]  # 当前 workflow 的最后一次 attempt 记录

    # 坏形状 attempt 不参与路径解析，避免把非字典对象误当成工件信息。
    if not isinstance(dict_latest_attempt, dict):

        # 直接返回 None，表示当前 surfaced artifact 不可解析。
        return None

    # 最终 artifact_dir 统一按 attempt 布局解析，兼容 external 脱敏占位值。
    return _resolved_attempt_artifact_dir(path_run_dir, dict_latest_attempt)

# _resolved_attempt_artifact_dir 兼容 external 占位值并重建 attempt 的本地 artifact 目录。
def _resolved_attempt_artifact_dir(
    path_run_dir: Path,
    dict_attempt: dict[str, Any],
) -> Path | None:
    """兼容 external 占位值并重建 attempt 的本地 artifact 目录。

    参数:
        path_run_dir: 当前 workflow run 的根目录。
        dict_attempt: 单个 workflow attempt 的结果字典。

    返回:
        返回已解析的 attempt artifact 目录；无法映射到本地目录时返回 None。
    """

    # artifact_dir 原始值优先按通用路径解析规则处理。
    raw_artifact_dir = dict_attempt.get("artifact_dir")  # attempt 记录里的 artifact_dir 原始值

    # 非 external 路径直接沿用现有解析结果。
    path_direct = _resolve_result_path(path_run_dir, raw_artifact_dir)  # artifact_dir 的直接解析结果

    # 已经拿到本地目录时，无需再走 attempt 布局重建。
    if path_direct is not None:

        # 返回已解析的本地 artifact 目录。
        return path_direct

    # 非字符串或非 external 占位值无法继续重建。
    if not isinstance(raw_artifact_dir, str) or not raw_artifact_dir.startswith("<external>/"):

        # 返回 None，保持 external 之外的坏形状显式暴露。
        return None

    # attempt_id 决定最终 artifact 所在的 attempt 子目录。
    value_attempt_id = dict_attempt.get("attempt_id")  # attempt 记录里的稳定编号

    # stage 决定最终 artifact 所在的最终阶段目录。
    value_stage = dict_attempt.get("stage")  # attempt 记录里的最终阶段名称

    # attempt_id 或 stage 缺失时不能可靠重建最终 artifact 目录。
    if (
        not isinstance(value_attempt_id, str)
        or not value_attempt_id
        or not isinstance(value_stage, str)
        or not value_stage
    ):

        # 返回 None，避免拼出没有证据支持的伪路径。
        return None

    # safe_path 的 external 占位只保留 basename，这里按固定 stage 布局回推真实目录。
    path_candidate = path_run_dir / value_attempt_id / value_stage / Path(raw_artifact_dir).name  # 按 stage 固定布局回推的本地 artifact 目录

    # 目录真实存在时，说明 external 占位已成功映射回本地目录。
    if path_candidate.exists():

        # 返回重建成功的本地 artifact 目录。
        return path_candidate

    # 目录不存在则说明当前 attempt 记录无法映射到本地可访问工件。
    return None

# _batch_case_summary 从单 case workflow 结果中提取批量摘要字段。
def _batch_case_summary(
    case_id: str,
    path_case_run_dir: Path,
    dict_result: dict[str, Any],
) -> dict[str, Any]:
    """从单 case workflow 结果中提取批量摘要字段。

    参数:
        case_id: 当前批量 case 的稳定标识。
        path_case_run_dir: 当前批量 case 的运行目录。
        dict_result: run_verilog_workflow 返回的稳定结果字典。

    返回:
        返回供 batch summary 复用的单 case 摘要字典。
    """

    # 先抽出 workflow_result 子字典，坏形状时回退为空。
    dict_workflow_result = dict_result.get("workflow_result", {}) if isinstance(dict_result, dict) else {}  # 单 case 的 workflow_result 子字典

    # attempts 列表只在 workflow_result 是字典时才继续读取。
    list_attempts: list[dict[str, Any]] = (
        dict_workflow_result.get("attempts", []) if isinstance(dict_workflow_result, dict) else []  # 当前单 case 的 attempts 列表
    )

    # 最新一次 attempt 单独提取出来，缺失时回退为空字典。
    dict_latest_attempt: dict[str, Any] = list_attempts[-1] if list_attempts else {}  # 最新一次 workflow attempt

    # validation 默认先记为未通过，只有文件存在时再覆盖。
    bool_validation_ok = False  # 当前单 case 的 validation 结果

    # semantic gate ready 状态默认留空，后面按文件结果补齐。
    value_semantic_gate_ready: bool | str | None = None  # 当前单 case 的 semantic gate ready 状态

    # validation.json 路径会在这里解析成本地可读路径。
    path_validation = _resolve_result_path(path_case_run_dir, dict_latest_attempt.get("validation_json"))  # validation.json 的本地路径

    # validation.json 存在时，继续提取其中的 ok 结果。
    if path_validation is not None and path_validation.exists():

        # validation.json 会先被读取成字典。
        dict_validation_payload = json.loads(path_validation.read_text(encoding="utf-8"))  # validation.json 的字典化内容

        # 再把 ok 字段收敛成布尔值。
        bool_validation_ok = bool(dict_validation_payload.get("ok"))  # validation 的布尔结果

    # 先确认最新 attempt 仍然是字典，避免读取 contract_paths 时越界。
    bool_has_attempt_dict = isinstance(dict_latest_attempt, dict)  # contract_paths 解析前的字典形状判断

    # 再抽出最新 attempt 里的 contract_paths，缺失时回退为空字典。
    dict_contract_paths = dict_latest_attempt.get("contract_paths") or {} if bool_has_attempt_dict else {}  # 最新 attempt 记录的 contract_paths 字典

    # 再从 contract_paths 中解析 stage_verification 的本地路径。
    path_stage_verification = _resolve_result_path(path_case_run_dir, dict_contract_paths.get("stage_verification"))  # 指向 stage_verification 证据文件的本地路径

    # stage_verification 存在时，再读取 ready 字段。
    if path_stage_verification is not None and path_stage_verification.exists():

        # stage_verification 文件同样先解析成字典。
        dict_stage_verification_payload = json.loads(path_stage_verification.read_text(encoding="utf-8"))  # stage_verification 文件的结构化内容

        # 最后把 ready 字段收进 batch 摘要。
        value_semantic_gate_ready = dict_stage_verification_payload.get("ready")  # 语义门 ready 状态

    # artifact_dir 在这里解析成本地路径，external 占位值则按 attempt 布局回推。
    path_artifact_dir = _resolved_attempt_artifact_dir(path_case_run_dir, dict_latest_attempt)  # artifact_dir 解析后的本地目录

    # 返回供 batch summary 复用的稳定摘要结构。
    return {
        "case_id": case_id,
        "status": str(dict_result.get("status") or "failed"),
        "run_dir": str(path_case_run_dir),
        "artifact_dir": path_artifact_dir.as_posix() if path_artifact_dir is not None else None,
        "validation_ok": bool_validation_ok,
        "semantic_gate_ready": value_semantic_gate_ready,
        "result_path": str(path_case_run_dir / "workflow_result.json"),
    }

# run_verilog_batch 提供多个 spec-to-RTL workflow 的批量执行入口。
def run_verilog_batch(
    specs: list[JsonSource],
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """批量运行多个 spec-to-RTL workflow，兼容旧关键字参数调用。

    参数:
        specs: 待批量运行的 spec 输入列表。
        config: 新式 facade 配置字典；缺省时只使用 legacy_options。
        legacy_options: 旧调用方传入的兼容关键字参数。

    返回:
        返回批量 workflow 的全局稳定摘要字典。

    异常:
        ValueError: 当 specs 为空或 out_dir 缺失时抛出。
    """

    # 先列出 batch 入口允许透传的兼容字段。
    str_batch_keys_text = (  # batch 入口允许透传的兼容键文本
        "out_dir workflow_config evidence "
        "provider_name provider_command generation_mode stream target "
        "design_requirements pipeline_required streamability interface_family interface_profile "
        "readiness max_attempts run_external external_target "
        "stop_on_human comment_language model_timeout_s"
    )

    # 再把字段文本转成查验 legacy 参数的集合。
    set_allowed_keys = set(str_batch_keys_text.split())  # run_verilog_batch 接收的兼容关键字集合

    # batch 配置在这里统一合并成一个字典视图。
    dict_options = _merged_option_dict("run_verilog_batch", config, legacy_options, allowed_keys=set_allowed_keys)  # run_verilog_batch 的统一配置视图

    # 空 spec 列表无法构成任何 batch 执行计划。
    if not specs:

        # 这里直接阻止空批量调用继续执行。
        raise ValueError("> ERR: [Python] run_verilog_batch requires at least one spec.")

    # batch 运行必须显式提供 out_dir 来承接子目录。
    if dict_options.get("out_dir") is None:

        # 缺失 out_dir 时无法为各 case 分配运行空间。
        raise ValueError("> ERR: [Python] run_verilog_batch requires `out_dir`.")

    # batch 根目录先在这里标准化。
    path_batch_root = Path(dict_options["out_dir"])  # 当前 batch workflow 的根目录

    # 写 case 子目录之前先确保 batch 根目录存在。
    path_batch_root.mkdir(parents=True, exist_ok=True)

    # 这个列表承接每个 case 的稳定摘要结果。
    list_case_results: list[dict[str, Any]] = []  # 当前 batch workflow 的单 case 摘要列表

    # 通过计数单独维护，便于最后汇总整体状态。
    int_passed_cases = 0  # 当前 batch workflow 中通过的 case 数量

    # 逐个 spec 进入 workflow，并保留输入顺序。
    for index, spec_item in enumerate(specs, start=1):

        # 当前 case_id 由 spec 与序号共同生成。
        str_case_id = _batch_case_id(spec_item, index)  # 当前批量 case 的稳定标识

        # case 运行目录采用序号加 case_id 的稳定命名。
        path_case_run_dir = path_batch_root / f"{index:03d}-{str_case_id}"  # 当前批量 case 的运行目录

        # 单 case 配置从 batch 配置复制，并改写 out_dir。
        dict_case_config = {**dict_options, "out_dir": path_case_run_dir, "resume_dir": None}  # 传给单 case workflow 的配置字典

        # 先执行单 case workflow，再从结果中提取摘要。
        dict_case_result = run_verilog_workflow(spec_item, config=dict_case_config)  # 当前批量 case 的 workflow 运行结果

        # 单 case 结果在这里转成稳定摘要结构。
        dict_case_summary = _batch_case_summary(str_case_id, path_case_run_dir, dict_case_result)  # 当前批量 case 的稳定摘要字典

        # 当前 case 摘要随后追加到 batch 汇总列表。
        list_case_results.append(dict_case_summary)

        # 只有 passed case 才计入最终通过数量。
        if dict_case_summary["status"] == "passed":

            # 这里单独累加 batch 的通过计数。
            int_passed_cases += 1  # 当前 case 通过时累加 batch 通过计数

    # 只有全部 case 通过时，batch 才记为 passed。
    str_status = "passed" if int_passed_cases == len(list_case_results) else "failed"  # 当前 batch workflow 的整体状态文本

    # 返回 batch 入口约定的全局稳定摘要结构。
    return {
        "status": str_status,
        "run_dir": str(path_batch_root),
        "summary": {
            "case_count": len(list_case_results),
            "passed_cases": int_passed_cases,
            "failed_cases": len(list_case_results) - int_passed_cases,
            "generation_mode": dict_options.get("generation_mode") or "regular",
        },
        "cases": list_case_results,
    }

# render_verilog_prompt 提供 Verilog 生成提示词渲染入口。
def render_verilog_prompt(
    spec: JsonSource,
    out_path: str | Path,
    *,
    config: dict[str, Any] | None = None,
    **legacy_options: Any,
) -> dict[str, Any]:
    """渲染 Verilog 生成 prompt，并写入调用方指定路径。

    参数:
        spec: 原始 spec 输入，可以是路径或内存态字典。
        out_path: prompt 文本的目标输出路径。
        config: 新式 facade 配置字典；缺省时只使用 legacy_options。
        legacy_options: 旧调用方传入的兼容关键字参数。

    返回:
        返回包含 prompt 输出路径和 prompt 文本的结果字典。
    """

    # 先把 prompt 入口允许透传的 legacy 键整理成稳定白名单文本。
    str_prompt_keys_text = (  # 用于生成 prompt legacy 白名单的键串
        "target design_requirements pipeline_required "
        "streamability interface_family interface_profile "
        "stage context_manifest context_dir evidence memory decision "
        "comment_language vector_contract subfunction budget"
    )

    # 再把字段文本切分成 legacy 参数校验集合。
    set_allowed_keys = set(str_prompt_keys_text.split())  # prompt legacy 参数校验使用的键集合

    # prompt 配置在这里合并成统一字典视图。
    dict_options = _merged_option_dict("render_verilog_prompt", config, legacy_options, allowed_keys=set_allowed_keys)  # prompt 渲染前整理出的统一配置视图

    # target 先在这里收敛到 facade 唯一支持的 rtl。
    str_target = _resolve_target(dict_options.get("target"), spec, {})  # render_verilog_prompt 解析出的 target

    # design_requirements 输入先统一读取成字典或空值。
    dict_design_requirements = _load_optional_json(dict_options.get("design_requirements"))  # prompt 渲染使用的设计约束字典

    # interface_profile 也在这里做同样的归一化。
    dict_interface_profile = _load_optional_json(dict_options.get("interface_profile"))  # prompt 渲染使用的接口细节字典

    # prompt 与 codegen plan 共用同一份规范化 spec。
    dict_resolved_spec = _prepare_facade_spec(  # prompt 阶段要消费的默认化 spec 视图
        spec,  # 保留调用方提交的原始 spec
        target=str_target,  # 锁定 prompt 阶段使用的目标类型
        design_requirements=dict_design_requirements, pipeline_required=dict_options.get("pipeline_required"),  # 把设计要求和流水线偏好带进 prompt
        streamability=dict_options.get("streamability"), interface_family=dict_options.get("interface_family"),  # 把流式化要求和接口族选择带进 prompt
        interface_profile=dict_interface_profile,  # 把接口细节约定带进 prompt
    )

    # codegen plan 在这里从规范化 spec 中派生。
    dict_resolved_codegen_plan = build_codegen_plan(dict_resolved_spec)  # prompt 渲染使用的 codegen 计划字典

    # context_manifest 会告诉 prompt 还需要显式展开哪些上下文条目。
    dict_context_manifest = _load_optional_json(dict_options.get("context_manifest"))  # prompt 需要显式展开的上下文清单

    # context_dir 会在这里标准化成 Path 或 None。
    path_context_dir = _optional_path(dict_options.get("context_dir"))  # prompt 补充上下文目录的根路径

    # evidence 会把已有验证证据拼接进 prompt 上下文。
    dict_evidence = _load_optional_json(dict_options.get("evidence"))  # prompt 追加的验证证据片段

    # memory 会把历史经验片段挂回当前 prompt。
    dict_memory = _load_optional_json(dict_options.get("memory"))  # prompt 复用的历史记忆片段

    # vector_contract 会补充向量接口在 prompt 阶段的约束。
    dict_vector_contract = _load_optional_json(dict_options.get("vector_contract"))  # prompt 追加的向量接口契约

    # decision 会把既有人审决策重新注入本轮 prompt。
    dict_decision = _load_optional_json(dict_options.get("decision"))  # prompt 复用的人审决策输入

    # comment_language 会影响最终 prompt 里的注释语种。
    str_comment_language = str(dict_options.get("comment_language", "zh"))  # prompt 渲染使用的注释语种

    # stage 缺省回落到 rtl，保持既有 prompt 合同不变。
    str_stage = str(dict_options.get("stage") or "rtl")  # prompt 渲染使用的阶段名称

    # budget 缺省回落到 normal，保持既有预算档位合同。
    str_budget = str(dict_options.get("budget", "normal"))  # prompt 渲染使用的预算档位

    # 最终 prompt 文本在这里一次性渲染完成。
    str_prompt_text = render_prompt(  # 最终 prompt 文本
        dict_resolved_spec,  # prompt 渲染使用的规范化 spec
        target="rtl", stage=str_stage,  # 锁定 prompt 的目标类型和阶段
        context_manifest=dict_context_manifest, context_dir=path_context_dir,  # 透传上下文清单和上下文目录
        evidence=dict_evidence, memory=dict_memory,  # 透传验证证据和历史记忆
        comment_language=str_comment_language, vector_contract=dict_vector_contract,  # 约束注释语种和向量接口契约
        codegen_plan=dict_resolved_codegen_plan, subfunction=dict_options.get("subfunction"),  # 透传代码计划和子功能提示
        budget=str_budget, decision=dict_decision,  # 透传预算档位和人审决策输入
    )

    # 输出路径会在这里标准化成 Path。
    path_output = Path(out_path)  # prompt 文本的目标输出路径

    # 写文件之前先保证目标父目录已经存在。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 把渲染好的 prompt 文本稳定写入目标路径。
    path_output.write_text(str_prompt_text, encoding="utf-8")

    # 返回包含路径与文本的稳定渲染结果。
    return {
        "path": str(path_output),
        "prompt": str_prompt_text,
    }
