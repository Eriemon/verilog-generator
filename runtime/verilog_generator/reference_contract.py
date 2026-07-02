"""构建 Python reference 合同并比对 Verilog 语义 transcript。"""

# 延迟注解解析，避免导入期解析嵌套 JSON 类型。
from __future__ import annotations

# 标准库负责动态加载 reference model、稳定序列化和合同哈希。
import hashlib
import importlib.util
import json
import types
import uuid
from pathlib import Path
from typing import Any

# transcript 中的语义结果行使用固定 tag，testbench 生成逻辑依赖该字符串。
REFERENCE_RESULT_TAG = "VERILOG-GEN-RESULT"  # Verilog transcript 语义结果标记

# audit_reference 是 Python stage 产出 reference 合同的公开入口。
def audit_reference(path: Path) -> dict[str, Any]:
    """审计 Python reference model 并生成稳定语义合同。

    参数:
        path: reference model 文件路径，或包含 model.py / *_model.py 的目录。

    返回:
        包含 case、输出键、checkpoint 键和 sha256 的 reference 合同字典。

    异常:
        reference model 缺少必要 API、向量格式非法或输出不稳定时抛出 ValueError。
    """

    # path_root 用于在目录输入时解析向量文件和相对模型路径。
    path_root = path if path.is_dir() else path.parent  # 向量搜索和相对路径计算根

    # path_model 是实际加载的 Python reference model 文件。
    path_model = _model_path(path)  # reference model 文件路径

    # module_type_reference 通过唯一模块名加载，避免污染全局模块缓存。
    module_type_reference: types.ModuleType = _load_module(path_model)  # 动态加载的 reference 模块

    # list_vectors 收集内联或 JSON 文件提供的参考用例。
    list_vectors = _reference_vectors(module_type_reference, path_root)  # reference 输入向量列表

    # func_run_tests 只做 API 存在性校验，保持旧版合同要求。
    func_run_tests = getattr(module_type_reference, "run_tests", None)  # reference 自测入口

    # func_run_case 是每个 case 生成 expected_outputs 的核心函数。
    func_run_case = getattr(module_type_reference, "run_case", None)  # 单用例执行入口

    # func_collect_checkpoints 可选提供中间语义观测点。
    func_collect_checkpoints = getattr(module_type_reference, "collect_checkpoints", None)  # checkpoint 采集入口

    # 缺少 run_tests 说明 reference model 不满足最小审计合同。
    if not callable(func_run_tests):

        # 错误前缀保持 current-project 终端可见文本规范。
        raise ValueError("> ERR: [Python] Python reference model must expose callable run_tests().")

    # run_case 必须可调用，否则无法为 case 生成期望输出。
    if not callable(func_run_case):

        # 错误消息保留 API 名称，便于模型或用户修复 reference 文件。
        raise ValueError("> ERR: [Python] Python reference model must expose callable run_case(case).")

    # collect_checkpoints 存在但不可调用时会破坏可选 checkpoint 合同。
    if func_collect_checkpoints is not None and not callable(func_collect_checkpoints):

        # 明确区分缺失和类型错误，缺失是允许的。
        raise ValueError("> ERR: [Python] collect_checkpoints must be callable when present.")

    # list_canonical_cases 保存最终写入合同的规范化用例。
    list_canonical_cases: list[dict[str, Any]] = []  # reference 合同 case 列表

    # value_output_signature 记录首个输出结构，用于发现后续 shape drift。
    value_output_signature: Any | None = None  # 首个用例确立的输出结构基准

    # value_checkpoint_signature 记录首个 checkpoint 结构，用于发现观测点漂移。
    value_checkpoint_signature: Any | None = None  # 跨用例 checkpoint 形状基准

    # list_output_keys 按首次出现顺序收集顶层输出字段。
    list_output_keys: list[str] = []  # testbench 需要比较的输出字段顺序

    # checkpoint 名称会进入 repair 提示，必须保留 reference 暴露的首见顺序。
    list_checkpoint_keys: list[str] = []  # repair 诊断提示可引用的观测点名称顺序

    # 逐个规范化 case 审计输出稳定性和合同字段。
    for dict_case in _canonical_cases(list_vectors):

        # str_case_id 是报告和 transcript 比对使用的稳定标识。
        str_case_id = _case_id(dict_case)  # 当前 reference case 标识

        # value_outputs_first 是第一次执行 run_case 得到的规范输出。
        value_outputs_first = _normalize_value(func_run_case(_clone(dict_case)))  # 确定性校验的首次输出

        # value_outputs_second 用于检测 run_case 是否具有确定性。
        value_outputs_second = _normalize_value(func_run_case(_clone(dict_case)))  # 同一输入的复跑输出

        # 同一个 case 两次输出不同表示 reference 自身不稳定。
        if value_outputs_first != value_outputs_second:

            # 非确定性 reference 不能作为 RTL 语义门禁基准。
            raise ValueError(f"> ERR: [Python] Reference model run_case is non-deterministic for {str_case_id!r}.")

        # value_checkpoints_first 是第一次 checkpoint 采集结果，缺少采集器时为 None。
        value_checkpoints_first = _collect_checkpoints_once(func_collect_checkpoints, dict_case)  # 首轮观测点快照

        # value_checkpoints_second 用于检测 checkpoint 采集是否稳定。
        value_checkpoints_second = _collect_checkpoints_once(func_collect_checkpoints, dict_case)  # 复跑观测点快照

        # checkpoint 两次结果不同会降低定位证据可信度。
        if value_checkpoints_first != value_checkpoints_second:

            # 错误中保留 case_id，方便定位不稳定输入。
            raise ValueError(
                f"> ERR: [Python] Reference model collect_checkpoints is non-deterministic for {str_case_id!r}."
            )

        # value_current_output_signature 描述当前输出 JSON 形状。
        value_current_output_signature = _shape_signature(value_outputs_first)  # 本用例输出形状

        # 首个 case 确定全局输出结构签名。
        if value_output_signature is None:

            # 后续 case 必须与该结构保持一致。
            value_output_signature = value_current_output_signature  # 后续输出字段对齐模板

        # 输出结构漂移会让 testbench 比对字段不稳定。
        elif value_output_signature != value_current_output_signature:

            # 将漂移 case 写入错误消息，便于收敛 reference 向量。
            raise ValueError(
                f"> ERR: [Python] Reference model output shape drift was detected at case {str_case_id!r}."
            )

        # checkpoint shape 的三元表达式拆开，避免长行遮住漂移判断。
        value_current_checkpoint_signature = (  # 当前 case 的 checkpoint 形状摘要
            _shape_signature(value_checkpoints_first)  # 非空 checkpoint 的嵌套结构
            if value_checkpoints_first is not None  # 只有提供观测点时才生成签名
            else None  # 未提供观测点时保留空基准
        )  # 本用例 checkpoint 结构对照值

        # 首个 checkpoint 形状作为后续 checkpoint 结构基准。
        if value_checkpoint_signature is None:

            # 没有 checkpoint 的 case 会保持基准为空。
            value_checkpoint_signature = value_current_checkpoint_signature  # checkpoint 字段一致性模板

        # 已建立 checkpoint 基准后，后续非空 checkpoint 必须保持一致。
        elif (
            value_current_checkpoint_signature is not None
            and value_checkpoint_signature != value_current_checkpoint_signature
        ):

            # checkpoint 漂移会破坏 subfunction 级定位。
            raise ValueError(
                f"> ERR: [Python] Reference model checkpoint shape drift was detected at case {str_case_id!r}."
            )

        # 将当前输出顶层键并入全局输出键列表。
        _extend_unique(list_output_keys, _top_level_keys(value_outputs_first))

        # 将当前 checkpoint 顶层键并入全局 checkpoint 键列表。
        _extend_unique(list_checkpoint_keys, _top_level_keys(value_checkpoints_first))

        # dict_entry 是写入 reference 合同的单个规范 case。
        dict_entry = {  # reference 合同单个用例
            "case_id": str_case_id,  # 稳定用例标识
            "inputs": _normalize_case_inputs(dict_case),  # 归一化后的输入字段
            "expected_outputs": value_outputs_first,  # 后续 RTL transcript 必须匹配的期望输出
        }

        # 只有 reference 提供 checkpoint 时才写入对应字段。
        if value_checkpoints_first is not None:

            # checkpoint 字段保持可选，兼容旧 reference model。
            dict_entry["checkpoints"] = value_checkpoints_first  # 用于 subfunction 定位的期望观测点

        # 记录当前规范 case，顺序由 _canonical_cases 保证稳定。
        list_canonical_cases.append(dict_entry)

    # str_canonical_json 用于生成稳定合同哈希。
    str_canonical_json = _canonical_cases_json(list_canonical_cases)  # 规范 case JSON 文本

    # str_case_hash 作为旧版 sha256 和 case_sha256 的共同值。
    str_case_hash = hashlib.sha256(str_canonical_json.encode("utf-8")).hexdigest()  # case 合同哈希

    # 返回结构保持旧版字段名称和嵌套形状。
    return {
        "version": 1,  # 合同版本
        "target": "python_reference",  # 合同目标类型
        "model": _model_summary(path, path_root, path_model, func_collect_checkpoints),  # reference model 摘要
        "case_count": len(list_canonical_cases),  # 合同包含的 case 数量
        "case_ids": [dict_entry["case_id"] for dict_entry in list_canonical_cases],  # case 顺序列表
        "output_keys": list_output_keys,  # 输出顶层字段集合
        "checkpoint_keys": list_checkpoint_keys,  # checkpoint 顶层字段集合
        "cases": list_canonical_cases,  # 完整规范 case 列表
        "sha256": str_case_hash,  # 工作流主路径读取的 reference 摘要哈希
        "case_sha256": str_case_hash,  # 旧版 case 哈希别名
    }

# parse_semantic_transcript 解析仿真日志中的 reference 结果行。
def parse_semantic_transcript(text: str) -> dict[str, Any]:
    """解析 Verilog transcript 中的语义结果 JSON 行。

    参数:
        text: 仿真日志或 transcript 完整文本。

    返回:
        包含 case_count、case_ids、cases 和 sha256 的 transcript 摘要。

    异常:
        结果 payload 不是 JSON object 或缺少 case_id 时抛出 ValueError。
    """

    # list_cases 按日志出现顺序保存 transcript case。
    list_cases: list[dict[str, Any]] = []  # 从日志 tag 中抽取出的结果行对象

    # 逐行扫描结果 tag，忽略普通仿真日志。
    for str_raw_line in text.splitlines():

        # str_line 去除首尾空白后用于 tag 检测。
        str_line = str_raw_line.strip()  # 去除空白后的单行仿真输出

        # 没有 tag 的日志行不参与语义解析。
        if not str_line or REFERENCE_RESULT_TAG not in str_line:

            # 跳过普通日志，继续扫描后续行。
            continue

        # str_payload_text 取出 tag 后面的 JSON 文本。
        str_payload_text = str_line.split(REFERENCE_RESULT_TAG, 1)[1].strip()  # tag 后载荷文本

        # tag 后允许有冒号分隔符，兼容生成的 testbench 文本。
        if str_payload_text.startswith(":"):

            # 去掉冒号后再解析 JSON。
            str_payload_text = str_payload_text[1:].strip()  # 去冒号后的载荷文本

        # dict_payload 是单条 transcript 语义结果对象。
        dict_payload = json.loads(str_payload_text)  # 解码后的 transcript 载荷对象

        # transcript payload 必须是对象，才能读取 case_id/status/outputs。
        if not isinstance(dict_payload, dict):

            # 非对象 payload 无法和 reference 合同比对。
            raise ValueError("> ERR: [Python] Transcript payload must be a JSON object.")

        # str_case_id 兼容 case_id 和 id 两种旧字段。
        str_case_id = str(dict_payload.get("case_id") or dict_payload.get("id") or "")  # 日志结果关联 case 键

        # 缺少 case_id 时无法建立 reference 对应关系。
        if not str_case_id:

            # 阻断无标识结果，避免把不同 case 混在一起。
            raise ValueError("> ERR: [Python] Transcript payload is missing case_id.")

        # dict_entry 规范化一条 transcript case。
        dict_entry = {  # transcript 中一行 tag 对应的规范结果
            "case_id": str_case_id,  # 与 reference 合同对齐的 case 键
            "status": str(dict_payload.get("status", "")).upper() or "UNKNOWN",  # 用例执行状态
            "outputs": _normalize_value(dict_payload.get("outputs")),  # RTL 实际输出
        }

        # checkpoints 字段保持可选，只有 testbench 打印时才纳入合同。
        if "checkpoints" in dict_payload:

            # checkpoint 也需要归一化后再比对。
            dict_entry["checkpoints"] = _normalize_value(dict_payload.get("checkpoints"))  # testbench 打印的观测点值

        # 按日志顺序登记 case，后续 case_order_drift 会使用该顺序。
        list_cases.append(dict_entry)

    # str_canonical_json 用于生成 transcript 稳定哈希。
    str_canonical_json = _canonical_cases_json(list_cases)  # 用于 transcript 摘要哈希的规范文本

    # 返回 transcript 摘要，供 reference 比较器按 case_id 消费。
    return {
        "case_count": len(list_cases),  # transcript 中 case 数量
        "case_ids": [dict_entry["case_id"] for dict_entry in list_cases],  # transcript case 顺序
        "cases": list_cases,  # transcript case 明细
        "sha256": hashlib.sha256(str_canonical_json.encode("utf-8")).hexdigest(),  # transcript 哈希
    }

# compare_reference_to_transcript 比对 Python reference 与 RTL transcript。
def compare_reference_to_transcript(reference_contract: dict[str, Any], transcript: dict[str, Any]) -> dict[str, Any]:
    """比较 reference 合同和 transcript 语义结果是否一致。

    参数:
        reference_contract: audit_reference 生成或同形状的 reference 合同。
        transcript: parse_semantic_transcript 生成或同形状的 transcript 摘要。

    返回:
        包含缺失、失败、输出漂移、checkpoint 漂移和定位置信度的比较报告。
    """

    # list_reference_cases 只保留对象形态的 reference case。
    list_reference_cases = [  # 过滤后的 reference case 对象
        dict_item  # reference 合同中的单个 case 字典
        for dict_item in reference_contract.get("cases", [])  # 原始 reference case 序列
        if isinstance(dict_item, dict)  # 只保留可读取字段的对象
    ]  # 可参与输出和 checkpoint 比对的 reference case

    # transcript 可能含有坏形态条目，比较前只留下可读字段的结果对象。
    list_transcript_cases = [  # 日志侧可比较的结果对象
        dict_item  # 单条仿真结果记录
        for dict_item in transcript.get("cases", [])  # parse_semantic_transcript 产出的原始序列
        if isinstance(dict_item, dict)  # 只保留能按字段比较的对象
    ]  # 可参与状态和输出比对的 transcript case

    # dict_transcript_by_id 便于按 case_id 查找 RTL 结果。
    dict_transcript_by_id = {  # transcript case_id 到结果对象的索引
        str(dict_item.get("case_id")): dict_item  # 单个日志 case 的查找项
        for dict_item in list_transcript_cases  # 遍历已经过滤的 transcript case
    }  # reference case 查找 RTL 观测结果的索引

    # set_reference_ids 用于识别 transcript 中多出的 case。
    set_reference_ids = {str(dict_item.get("case_id")) for dict_item in list_reference_cases}  # 合法 case_id 范围

    # list_mismatched_cases 收集 expected_outputs 与 outputs 不一致的 case。
    list_mismatched_cases: list[dict[str, Any]] = []  # 输出漂移 case 列表

    # list_checkpoint_drift 收集 checkpoint 字段不一致的 case。
    list_checkpoint_drift: list[dict[str, Any]] = []  # checkpoint 漂移 case 列表

    # list_failed_cases 收集 transcript 明确非 PASS 的 case。
    list_failed_cases: list[str] = []  # transcript 失败 case 列表

    # list_missing_cases 收集 reference 有但 transcript 缺失的 case。
    list_missing_cases: list[str] = []  # transcript 缺失 case 列表

    # list_extra_cases 收集 transcript 中不属于 reference 合同的 case。
    list_extra_cases = _extra_transcript_cases(list_transcript_cases, set_reference_ids)  # 未被 reference 声明的日志 case

    # 逐个 reference case 查找并比较 transcript 结果。
    for dict_reference_case in list_reference_cases:

        # str_case_id 是 reference 和 transcript 的关联键。
        str_case_id = str(dict_reference_case.get("case_id"))  # 正在比对的 reference case 键

        # dict_transcript_case 是同一 case 的 RTL transcript 结果。
        dict_transcript_case = dict_transcript_by_id.get(str_case_id)  # 与当前 reference 对应的日志结果

        # transcript 缺少该 case 时记录缺失并跳过值比较。
        if not dict_transcript_case:

            # 缺失 case 会阻断 semantic_ready。
            list_missing_cases.append(str_case_id)

            # 无对应 transcript 时没有可比对的 outputs。
            continue

        # 非 PASS 状态说明 testbench 明确报告失败。
        if str(dict_transcript_case.get("status", "")).upper() != "PASS":

            # 失败 case 单独记录，便于上游生成诊断。
            list_failed_cases.append(str_case_id)

        # value_expected_outputs 是 reference 期望输出。
        value_expected_outputs = _normalize_value(dict_reference_case.get("expected_outputs"))  # Python 模型给出的金标准输出

        # value_observed_outputs 是 transcript 实际输出。
        value_observed_outputs = _normalize_value(dict_transcript_case.get("outputs"))  # RTL 仿真记录的实际输出

        # 输出不一致时记录详细漂移键和值。
        if value_expected_outputs != value_observed_outputs:

            # 漂移明细帮助定位输出字段或数组位置。
            list_mismatched_cases.append(
                {
                    "case_id": str_case_id,  # 出现输出漂移的 case 键
                    "drift_keys": _drift_keys(value_expected_outputs, value_observed_outputs),  # 输出字段差异路径
                    "expected_outputs": value_expected_outputs,  # Python reference 输出快照
                    "observed_outputs": value_observed_outputs,  # RTL 日志中该 case 的输出快照
                }
            )

        # value_expected_checkpoints 缺失时不强制 transcript 提供 checkpoint。
        value_expected_checkpoints = _optional_normalized_field(  # reference 侧可选观测点
            dict_reference_case,  # 当前 reference case 字典
            "checkpoints",  # 只读取 checkpoint 合同字段
        )  # Python 模型声明的中间观测值

        # value_observed_checkpoints 缺失时同样不参与 checkpoint 值比较。
        value_observed_checkpoints = _optional_normalized_field(  # RTL 日志提供的 checkpoint 候选
            dict_transcript_case,  # 当前 RTL 日志结果字典
            "checkpoints",  # 只读取 checkpoint 输出字段
        )  # RTL testbench 实际打印的中间观测值

        # 两侧都提供 checkpoint 且值不同才报告 checkpoint drift。
        if (
            value_expected_checkpoints is not None
            and value_observed_checkpoints is not None
            and value_expected_checkpoints != value_observed_checkpoints
        ):

            # checkpoint 漂移可用于定位具体 subfunction 或观测点。
            list_checkpoint_drift.append(
                {
                    "case_id": str_case_id,  # 出现 checkpoint 漂移的 case 键
                    "drift_keys": _drift_keys(value_expected_checkpoints, value_observed_checkpoints),  # 观测点差异路径
                    "expected_checkpoints": value_expected_checkpoints,  # Python reference 观测点
                    "observed_checkpoints": value_observed_checkpoints,  # RTL 日志中的观测点值
                }
            )

    # list_order_drift 标记 reference 和 transcript 同位置 case_id 不一致的位置。
    list_order_drift = _case_order_drift(reference_contract, transcript)  # case 顺序漂移列表

    # bool_semantic_ready 表示 transcript 覆盖、状态和输出都已通过。
    bool_semantic_ready = not list_missing_cases and not list_failed_cases and not list_mismatched_cases  # 语义比对是否通过

    # float_localization_confidence 根据输出和 checkpoint 漂移估计定位可信度。
    float_localization_confidence = _localization_confidence(list_mismatched_cases, list_checkpoint_drift)  # 定位置信度

    # 返回字段保持旧版 semantic_execution 形状。
    return {
        "semantic_ready": bool_semantic_ready,  # 语义比对总体结果
        "mismatched_cases": list_mismatched_cases,  # 输出漂移明细
        "checkpoint_drift": list_checkpoint_drift,  # checkpoint 漂移明细
        "failed_cases": list_failed_cases,  # transcript 失败 case
        "missing_cases": list_missing_cases,  # transcript 缺失 case
        "extra_cases": list_extra_cases,  # transcript 额外 case
        "case_order_drift": list_order_drift,  # case 顺序漂移
        "localization_confidence": float_localization_confidence,  # repair 阶段排序 suspect 的置信分
        "reference_sha256": reference_contract.get("sha256"),  # Python reference 审计哈希
        "transcript_sha256": transcript.get("sha256"),  # RTL 日志语义摘要哈希
    }

# _model_path 解析文件或目录输入对应的 reference model 文件。
def _model_path(path: Path) -> Path:
    """定位可审计的 Python reference model 文件。

    参数:
        path: 直接的 Python 文件路径，或待搜索 model.py / *_model.py 的目录。

    返回:
        唯一的 Python reference model 文件路径。

    异常:
        目录内找不到模型或找到多个候选时抛出 ValueError。
    """

    # 文件输入直接作为 reference model。
    if path.is_file():

        # 返回调用方指定的模型文件。
        return path

    # list_candidates 同时支持 model.py 和 *_model.py 命名。
    list_candidates = sorted({*path.glob("**/*_model.py"), *path.glob("**/model.py")})  # 目录搜索得到的模型候选

    # 没有候选时无法继续审计。
    if not list_candidates:

        # 错误提示保留 Python reference model 关键词。
        raise ValueError("> ERR: [Python] No Python reference model file was found.")

    # 多个候选会导致审计目标不明确。
    if len(list_candidates) > 1:

        # 调用方应传入明确文件路径或清理候选。
        raise ValueError(
            "> ERR: [Python] Multiple Python reference model files were found; audit-reference expects exactly one."
        )

    # 返回唯一候选文件。
    return list_candidates[0]

# _load_module 使用唯一模块名加载 reference model。
def _load_module(path_model: Path) -> types.ModuleType:
    """从文件路径动态加载 Python reference module。

    参数:
        path_model: 待加载的 reference model Python 文件。

    返回:
        已执行并可访问 API 的模块对象。

    异常:
        Python importlib 无法构造 loader 时抛出 ValueError。
    """

    # str_module_name 避免不同 reference model 之间模块名冲突。
    str_module_name = f"verilog_generator_reference_{uuid.uuid4().hex}"  # 动态模块名

    # config_module_spec 描述 importlib 如何从文件加载模块。
    config_module_spec = importlib.util.spec_from_file_location(str_module_name, path_model)  # 文件导入所需 loader 描述

    # spec 或 loader 缺失说明该路径不可作为 Python 模块加载。
    if config_module_spec is None or config_module_spec.loader is None:

        # 错误中包含路径，便于定位坏文件。
        raise ValueError(f"> ERR: [Python] Could not load Python reference model from {path_model}.")

    # module_type_reference 是尚未执行源码的新模块对象。
    module_type_reference = importlib.util.module_from_spec(config_module_spec)  # 尚未执行源码的模块容器

    # 执行模块源码，使 run_case 等 API 可读取。
    config_module_spec.loader.exec_module(module_type_reference)

    # 返回加载后的模块对象。
    return module_type_reference

# _reference_vectors 读取 reference module 或 vectors.json 中的 case。
def _reference_vectors(module_type_reference: types.ModuleType, path_root: Path) -> list[Any]:
    """读取 reference model 提供的输入向量列表。

    参数:
        module_type_reference: 已加载的 reference model 模块。
        path_root: 搜索 vectors.json 的根目录。

    返回:
        reference 输入 case 原始列表。

    异常:
        REFERENCE_VECTORS 或 vectors.json 不是列表形态时抛出 ValueError。
    """

    # 模块内联 REFERENCE_VECTORS 优先于磁盘 JSON 文件。
    if hasattr(module_type_reference, "REFERENCE_VECTORS"):

        # list_vectors_candidate 保留原始对象，随后验证列表形态。
        list_vectors_candidate = getattr(module_type_reference, "REFERENCE_VECTORS")  # 模块声明的候选向量

        # 内联向量必须是 list，避免 dict 键顺序产生歧义。
        if not isinstance(list_vectors_candidate, list):

            # 明确指出 REFERENCE_VECTORS 类型错误。
            raise ValueError("> ERR: [Python] REFERENCE_VECTORS must be a list.")

        # 返回模块内联向量。
        return list_vectors_candidate

    # list_vector_paths 查找目录下第一个 vectors.json 文件。
    list_vector_paths = sorted(path_root.glob("**/*vectors.json"))  # vectors JSON 候选路径

    # 没有内联向量也没有 JSON 文件时无法审计。
    if not list_vector_paths:

        # 提示两种允许来源。
        raise ValueError(
            "> ERR: [Python] Python reference model must provide REFERENCE_VECTORS or a vectors.json file."
        )

    # value_payload 是 vectors.json 解码后的对象或列表。
    value_payload = json.loads(list_vector_paths[0].read_text(encoding="utf-8"))  # 向量文件原始 JSON 载荷

    # value_raw_cases 兼容 {"cases": [...]} 和直接列表两种格式。
    value_raw_cases = value_payload.get("cases", value_payload) if isinstance(value_payload, dict) else value_payload  # JSON 中的候选 case 集合

    # vectors JSON 最终必须提供 case 列表。
    if not isinstance(value_raw_cases, list):

        # 非列表 case 结构无法稳定排序和执行。
        raise ValueError("> ERR: [Python] Reference vectors JSON must contain a cases list.")

    # 返回 JSON 中的 case 列表。
    return value_raw_cases

# _canonical_cases 归一化并稳定排序 reference 输入向量。
def _canonical_cases(list_vectors: list[Any]) -> list[dict[str, Any]]:
    """把 reference 输入向量归一化为稳定排序的 case 列表。

    参数:
        list_vectors: reference model 或 vectors.json 提供的原始 case。

    返回:
        按 id/name/JSON 文本排序后的 case 字典列表。
    """

    # list_normalized_cases 保证后续 run_case 输入可 JSON 序列化。
    list_normalized_cases = [_normalize_value(obj_case) for obj_case in list_vectors]  # 规范化 case 列表

    # list_sorted_cases 使用稳定 key 排序，消除输入顺序差异。
    list_sorted_cases = sorted(list_normalized_cases, key=_case_sort_key)  # 排序后的 case 列表

    # 返回规范 case 列表。
    return list_sorted_cases

# _case_sort_key 生成 reference case 的稳定排序键。
def _case_sort_key(dict_case: dict[str, Any]) -> str:
    """生成 reference case 的排序键。

    参数:
        dict_case: 已规范化的单个 reference case。

    返回:
        优先使用 id/name，否则使用稳定 JSON 文本的排序键。
    """

    # value_case_id 优先使用显式 id。
    value_case_id = dict_case.get("id")  # 排序优先级最高的显式标识

    # value_case_name 作为 id 缺失时的备选标识。
    value_case_name = dict_case.get("name")  # 无 id 时使用的人类可读名称

    # 返回显式 id/name 或 JSON 文本排序键。
    return str(value_case_id or value_case_name or json.dumps(dict_case, sort_keys=True, ensure_ascii=False))

# _clone 通过 JSON 往返复制 reference case。
def _clone(obj_value: Any) -> Any:
    """复制一个已可 JSON 序列化的 reference 值。

    参数:
        obj_value: 需要复制的任意 reference case 或输出值。

    返回:
        与输入语义等价但不会共享容器引用的新对象。
    """

    # JSON 往返复用 normalize 逻辑，避免 reference run_case 修改原始 case。
    return json.loads(json.dumps(_normalize_value(obj_value), ensure_ascii=False))

# _collect_checkpoints_once 封装可选 checkpoint 采集器调用。
def _collect_checkpoints_once(func_collect_checkpoints: Any, dict_case: dict[str, Any]) -> Any | None:
    """执行一次可选 checkpoint 采集。

    参数:
        func_collect_checkpoints: reference model 中的 collect_checkpoints 函数或 None。
        dict_case: 当前 reference case。

    返回:
        归一化后的 checkpoint 对象；没有采集器时返回 None。
    """

    # 缺少采集器时 reference 合同不包含 checkpoint。
    if not callable(func_collect_checkpoints):

        # None 表示该 reference model 未提供 checkpoint。
        return None

    # value_checkpoints 是采集器针对当前 case 的原始结果。
    value_checkpoints = func_collect_checkpoints(_clone(dict_case))  # 采集器返回的观测点原值

    # 返回 JSON 归一化后的 checkpoint 结果。
    return _normalize_value(value_checkpoints)

# _normalize_case_inputs 提取合同中展示的输入字段。
def _normalize_case_inputs(dict_case: dict[str, Any]) -> Any:
    """从 reference case 中提取可展示的输入对象。

    参数:
        dict_case: 已规范化的 reference case。

    返回:
        inputs/input 字段内容，或剔除 id/name/expected/output 后的剩余字段。
    """

    # 非对象 case 原样作为输入展示。
    if not isinstance(dict_case, dict):

        # 返回原值以保持异常输入的兼容展示。
        return dict_case

    # inputs 对象是最明确的输入字段。
    if "inputs" in dict_case and isinstance(dict_case["inputs"], dict):

        # 返回规范化 inputs 内容。
        return _normalize_value(dict_case["inputs"])

    # 单数 input 字段兼容早期 reference 样例中的简写格式。
    if "input" in dict_case:

        # 单输入 case 的 payload 仍需要进入统一 JSON 规范化。
        return _normalize_value(dict_case["input"])

    # dict_inputs 删除期望输出和标识字段后作为输入摘要。
    dict_inputs = {  # 从 case 中推导出的输入字段
        str_key: obj_item  # 保留输入候选字段和值
        for str_key, obj_item in dict_case.items()  # 遍历原始 case 字段
        if str_key not in {"id", "name", "expected", "outputs", "output"}  # 排除标识和期望输出字段
    }

    # 返回推导输入字段的规范化形式。
    return _normalize_value(dict_inputs)

# _case_id 读取 reference case 的稳定标识。
def _case_id(dict_case: dict[str, Any]) -> str:
    """返回 reference case 的稳定标识。

    参数:
        dict_case: 已规范化的 reference case 字典。

    返回:
        id/name 字段字符串；缺失时返回兼容旧行为的 case。
    """

    # 返回显式 id/name，缺失时使用旧版默认值。
    return str(dict_case.get("id") or dict_case.get("name") or "case")

# _normalize_value 把 reference 或 transcript 值转换为 JSON 稳定结构。
def _normalize_value(obj_value: Any) -> Any:
    """归一化 reference 和 transcript 中的可比较值。

    参数:
        obj_value: 任意待写入合同或参与比对的值。

    返回:
        None、标量、按键排序的 dict 或 list 组成的 JSON 兼容结构。

    异常:
        遇到无法转为 JSON 语义结构的对象时抛出 ValueError。
    """

    # JSON 标量可以直接进入合同。
    if obj_value is None or isinstance(obj_value, (bool, int, float, str)):

        # 返回原始标量值。
        return obj_value

    # 字典按字符串键排序，保证合同哈希稳定。
    if isinstance(obj_value, dict):

        # 返回按键排序后的规范字典。
        return {str(str_key): _normalize_value(obj_value[str_key]) for str_key in sorted(obj_value)}

    # 列表和元组统一转成 list，便于 JSON 序列化。
    if isinstance(obj_value, (list, tuple)):

        # 返回逐项规范化后的列表。
        return [_normalize_value(obj_item) for obj_item in obj_value]

    # numpy 等对象若提供 tolist，则转为 Python 容器。
    if hasattr(obj_value, "tolist"):

        # tolist 结果继续走 normalize，覆盖数组和标量数组。
        return _normalize_value(obj_value.tolist())

    # numpy 标量等对象若提供 item，则转为 Python 标量。
    if hasattr(obj_value, "item"):

        # item 结果继续走 normalize，覆盖数值标量。
        return _normalize_value(obj_value.item())

    # 不支持的对象类型不能写入稳定语义合同。
    raise ValueError(f"> ERR: [Python] Unsupported value type in semantic contract: {type(obj_value).__name__}")

# _shape_signature 生成值结构签名，用于跨 case 漂移检测。
def _shape_signature(obj_value: Any) -> Any:
    """生成 reference 输出或 checkpoint 的结构签名。

    参数:
        obj_value: 已规范化的 reference 输出或 checkpoint 对象。

    返回:
        保留 dict 键、list 嵌套和标量类型名的结构签名。
    """

    # 字典保留键名并递归描述每个字段结构。
    if isinstance(obj_value, dict):

        # 返回字段级结构签名。
        return {str_key: _shape_signature(obj_item) for str_key, obj_item in obj_value.items()}

    # 列表只记录首项结构，保持旧版 shape 语义。
    if isinstance(obj_value, list):

        # 空列表和非空列表分别给出空签名或首项签名。
        return [_shape_signature(obj_value[0])] if obj_value else []

    # 标量使用类型名表达结构。
    return type(obj_value).__name__

# _top_level_keys 返回对象顶层字段名。
def _top_level_keys(obj_value: Any) -> list[str]:
    """读取 dict 值的顶层键名。

    参数:
        obj_value: reference 输出或 checkpoint 对象。

    返回:
        dict 顶层键名列表；非 dict 返回空列表。
    """

    # 只有 dict 形态才存在顶层语义字段。
    if isinstance(obj_value, dict):

        # 保留当前 dict 的迭代顺序。
        return list(obj_value.keys())

    # 非 dict 输出没有可汇总的顶层字段。
    return []

# _extend_unique 保持字段首次出现顺序。
def _extend_unique(list_target: list[str], list_items: list[str]) -> None:
    """把新字段按首次出现顺序追加到目标列表。

    参数:
        list_target: 需要原地更新的字符串列表。
        list_items: 候选字符串字段列表。

    返回:
        没有业务返回值；list_target 会被原地扩展。
    """

    # 逐个字段判断是否已经登记。
    for str_item in list_items:

        # 已存在字段不重复追加，保持首见顺序。
        if str_item not in list_target:

            # 新字段进入目标列表。
            list_target.append(str_item)

# _canonical_cases_json 生成 case 列表的稳定 JSON 文本。
def _canonical_cases_json(list_cases: list[dict[str, Any]]) -> str:
    """生成 case 列表对应的稳定 JSON 文本。

    参数:
        list_cases: reference 或 transcript 的 case 列表。

    返回:
        使用固定 separators、排序键和 UTF-8 语义的 JSON 字符串。
    """

    # dict_payload 统一外层键，保持旧版哈希输入形状。
    dict_payload = {"cases": list_cases}  # case 哈希载荷

    # 返回稳定 JSON 文本。
    return json.dumps(dict_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

# _model_summary 构造 reference model 审计摘要。
def _model_summary(
    path_input: Path,
    path_root: Path,
    path_model: Path,
    func_collect_checkpoints: Any,
) -> dict[str, Any]:
    """构造 reference 合同中的 model 摘要字段。

    参数:
        path_input: audit_reference 调用方传入的原始路径。
        path_root: reference 根目录。
        path_model: 实际加载的 model 文件。
        func_collect_checkpoints: 可选 checkpoint 采集函数或 None。

    返回:
        包含模型路径和 API 能力摘要的字典。
    """

    # str_model_path 文件输入时只保留文件名，目录输入时保留相对路径。
    str_model_path = path_model.name if path_input.is_file() else path_model.relative_to(path_root).as_posix()  # 合同展示路径

    # model 小节只暴露路径和 API 能力，避免把动态模块对象写入 JSON。
    dict_summary = {  # reference 合同里的模型来源摘要
        "path": str_model_path,  # reference model 相对展示路径
        "api_summary": {  # workflow 展示的 reference API 能力
            "has_run_tests": True,  # run_tests 已确认可调用
            "has_run_case": True,  # run_case 已确认可为 case 产出期望值
            "has_collect_checkpoints": callable(func_collect_checkpoints),  # 是否具备观测点采集能力
        },
    }

    # 返回 model 摘要。
    return dict_summary

# _optional_normalized_field 读取可选字段并归一化。
def _optional_normalized_field(dict_payload: dict[str, Any], str_field_name: str) -> Any | None:
    """读取字典中的可选字段并做语义归一化。

    参数:
        dict_payload: reference case 或 transcript case 字典。
        str_field_name: 需要读取的字段名。

    返回:
        字段存在时返回归一化值；字段缺失时返回 None。
    """

    # 缺失字段表示当前合同不要求比较该语义区域。
    if str_field_name not in dict_payload:

        # None 与缺失字段保持一致。
        return None

    # 返回字段值的规范化形式。
    return _normalize_value(dict_payload.get(str_field_name))

# _extra_transcript_cases 找出 reference 未声明的 transcript case。
def _extra_transcript_cases(list_transcript_cases: list[dict[str, Any]], set_reference_ids: set[str]) -> list[str]:
    """识别 transcript 中多出的 case_id。

    参数:
        list_transcript_cases: transcript 解析出的 case 列表。
        set_reference_ids: reference 合同声明的 case_id 集合。

    返回:
        transcript 中存在但 reference 合同未声明的 case_id 字符串列表。
    """

    # 返回额外 case_id，供比较报告暴露未授权覆盖。
    return [
        str(dict_item.get("case_id"))  # 日志额外 case 键
        for dict_item in list_transcript_cases  # 遍历 transcript case
        if str(dict_item.get("case_id")) not in set_reference_ids and dict_item.get("case_id")  # 排除已声明的 reference case
    ]

# _case_order_drift 检查 reference 与 transcript 同位置 case_id 是否一致。
def _case_order_drift(reference_contract: dict[str, Any], transcript: dict[str, Any]) -> list[Any]:
    """计算 reference 和 transcript 的 case 顺序漂移。

    参数:
        reference_contract: reference 合同字典。
        transcript: transcript 摘要字典。

    返回:
        两侧同一位置 case_id 不一致时的 reference case_id 列表。
    """

    # list_order_drift 只记录 reference 侧期望顺序中的漂移项。
    list_order_drift = [  # reference 顺序中被 transcript 打乱的位置
        obj_case_id  # reference 原始顺序中的 case id
        for obj_case_id, obj_observed in zip(  # 同索引比较 reference 与 transcript
            reference_contract.get("case_ids", []),  # reference 合同 case_id 顺序
            transcript.get("case_ids", []),  # transcript 实际打印顺序
        )  # 同位置 case_id 比较输入
        if obj_case_id != obj_observed  # case id 不一致时认为顺序漂移
    ]

    # 返回顺序漂移列表。
    return list_order_drift

# _drift_keys 递归计算 expected 和 observed 的差异路径。
def _drift_keys(obj_expected: Any, obj_observed: Any, str_prefix: str = "") -> list[str]:
    """返回 expected 与 observed 不一致的字段路径。

    参数:
        obj_expected: reference 期望值。
        obj_observed: transcript 观测值。
        str_prefix: 递归过程中已经进入的字段路径前缀。

    返回:
        所有差异字段或列表位置的路径列表。
    """

    # dict-dict 比较按键集合递归。
    if isinstance(obj_expected, dict) and isinstance(obj_observed, dict):

        # list_drift_keys 保存当前 dict 子树下的所有差异路径。
        list_drift_keys: list[str] = []  # 字典字段差异路径

        # 逐个合并键检查缺失或子字段漂移。
        for str_key in sorted(set(obj_expected) | set(obj_observed)):

            # str_next_prefix 是当前字段的路径表达。
            str_next_prefix = f"{str_prefix}.{str_key}" if str_prefix else str(str_key)  # 子字段路径

            # 任一侧缺少字段时直接记录当前路径。
            if str_key not in obj_expected or str_key not in obj_observed:

                # 缺失字段就是最小漂移路径。
                list_drift_keys.append(str_next_prefix)

            # 两侧都有字段时继续递归比较。
            else:

                # 子字段漂移并入当前结果。
                list_drift_keys.extend(
                    _drift_keys(obj_expected[str_key], obj_observed[str_key], str_next_prefix)
                )

        # 返回当前 dict 子树的漂移路径。
        return list_drift_keys

    # list-list 比较按最长长度递归。
    if isinstance(obj_expected, list) and isinstance(obj_observed, list):

        # int_length 覆盖两侧列表长度，缺项也会被记录。
        int_length = max(len(obj_expected), len(obj_observed))  # 列表比较长度

        # 列表比较分支把长度差异和元素差异统一登记为索引路径。
        list_drift_keys: list[str] = []  # 当前列表分支发现的索引漂移路径

        # 按位置比较列表元素。
        for int_index in range(int_length):

            # str_next_prefix 是当前列表索引路径。
            str_next_prefix = f"{str_prefix}[{int_index}]" if str_prefix else f"[{int_index}]"  # 子元素路径

            # 任一侧索引越界时记录当前元素路径。
            if int_index >= len(obj_expected) or int_index >= len(obj_observed):

                # 长度漂移以具体索引形式记录。
                list_drift_keys.append(str_next_prefix)

            # 两侧都有该索引时继续递归比较。
            else:

                # 子元素漂移并入当前结果。
                list_drift_keys.extend(
                    _drift_keys(obj_expected[int_index], obj_observed[int_index], str_next_prefix)
                )

        # 列表分支结束时返回按索引聚合的漂移路径。
        return list_drift_keys

    # 标量或不同类型值不一致时记录当前路径。
    if obj_expected != obj_observed:

        # 空前缀表示顶层值本身漂移。
        return [str_prefix or "<value>"]

    # 完全一致时没有漂移路径。
    return []

# _localization_confidence 根据漂移证据估计定位置信度。
def _localization_confidence(
    list_mismatched_cases: list[dict[str, Any]],
    list_checkpoint_drift: list[dict[str, Any]],
) -> float:
    """根据输出和 checkpoint 漂移估计定位置信度。

    参数:
        list_mismatched_cases: 输出值不一致的 case 列表。
        list_checkpoint_drift: checkpoint 值不一致的 case 列表。

    返回:
        旧版工作流使用的定位置信度分数。
    """

    # checkpoint 漂移提供更强的内部定位证据。
    if list_checkpoint_drift:

        # 有 checkpoint 证据时使用较高置信度。
        return 0.85

    # 单个输出漂移 case 通常比多个 case 更易定位。
    if len(list_mismatched_cases) == 1:

        # 单 case 输出漂移使用中等置信度。
        return 0.6

    # 多个输出漂移 case 表示问题范围更宽。
    if list_mismatched_cases:

        # 多 case 漂移使用较低置信度。
        return 0.35

    # 没有漂移时语义定位视为完全稳定。
    return 1.0
