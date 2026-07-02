"""整理 reference vector 的稳定语义摘要，供提示词和验证链路比对。"""

# 延迟注解解析，避免运行期导入时提前求值类型提示。
from __future__ import annotations

# 标准库依赖只承担 JSON 规范化、摘要和路径扫描职责。
import hashlib
import json
from pathlib import Path
from typing import Any

# 生成物中嵌入的向量摘要前缀，用于从模型输出或验证日志提取契约哈希。
VECTOR_HASH_TAG = "VERILOG-GEN-VECTORS-SHA256:"  # reference vector 哈希标记

# 公开文件入口负责把磁盘 JSON 纳入稳定契约生成流程。
def audit_vectors(vectors_path: Path) -> dict[str, Any]:
    """读取 reference vector JSON 文件并生成稳定契约。

    参数:
        vectors_path: 指向 reference vector JSON 的本地路径。

    返回:
        包含 sha256、case 数量、case id 和输入输出键集合的契约字典。
    """

    # 文件内容必须按 UTF-8 读取，确保中文 case 名参与哈希时稳定。
    payload: Any = json.loads(vectors_path.read_text(encoding="utf-8"))  # JSON 原始负载

    # 契约保留源文件路径，方便上层报告定位生成物来源。
    return vector_contract_from_payload(payload, source=str(vectors_path))

# 公开负载入口负责把调用方传入的 JSON 对象转换为契约字典。
def vector_contract_from_payload(payload: Any, *, source: str | None = None) -> dict[str, Any]:
    """把 reference vector 负载转换成可复现的语义契约。

    参数:
        payload: JSON 解码后的 list，或包含 cases/vectors 字段的 dict。
        source: 可选的来源说明，通常是 vectors 文件路径。

    返回:
        稳定排序、稳定编码后的契约字典，字段名保持对外兼容。
    """

    # 先统一支持 list、cases 和 vectors 三种历史输入形态。
    list_cases = _cases_from_payload(payload)  # 原始 case 列表

    # 先消除每个 case 内部 dict 键顺序差异。
    tuple_normalized_cases = tuple(  # JSON 字段乱序仍生成同一 sha256
        _normalize_json(raw_case)  # 单个 case 的递归规范值
        for raw_case in list_cases  # 原始 reference vector 用例
    )

    # case 内容递归排序后再排序列表，保证相同语义得到相同哈希。
    list_normalized_cases = sorted(tuple_normalized_cases, key=_case_sort_key)  # 哈希覆盖用例顺序

    # 哈希只覆盖 cases 字段，避免 source/path 这类环境信息影响契约。
    dict_canonical = {"cases": list_normalized_cases}  # 哈希输入对象

    # 紧凑 JSON 编码让跨平台哈希不受空白字符影响。
    str_canonical_json = json.dumps(  # 规范 JSON 文本
        dict_canonical,  # 待编码契约核心对象
        ensure_ascii=False,  # 保留中文可读文本
        sort_keys=True,  # 字典键参与稳定排序
        separators=(",", ":"),  # 移除 JSON 空白差异
    )

    # case id 用于提示词、报告和 transcript 之间建立稳定对应关系。
    list_case_ids = [  # 稳定 case 标识
        _case_id(normalized_case, case_index)  # 单个 case 的稳定标识
        for case_index, normalized_case in enumerate(list_normalized_cases, start=1)  # 一基 case 序号
    ]

    # 契约字段名属于外部 JSON 形状，不能随局部命名规则调整。
    dict_contract = {  # 对外契约字典
        "version": 1,  # 契约结构版本
        "sha256": hashlib.sha256(str_canonical_json.encode("utf-8")).hexdigest(),  # 契约内容哈希
        "case_count": len(list_normalized_cases),  # 契约覆盖的 case 数量
        "case_ids": list_case_ids,  # transcript 对齐用 case 标识
        "input_keys": _keys_for(list_normalized_cases, ("inputs", "input")),  # 输入侧可见键集合
        "output_keys": _keys_for(list_normalized_cases, ("outputs", "expected", "output")),  # 输出侧可见键集合
        "canonical_json": str_canonical_json,  # 审计复现用规范 JSON 文本
    }

    # 只有调用方提供来源时才把环境相关信息附加到报告。
    if source:

        # source 不参与哈希，只作为定位线索传递给上层。
        dict_contract["source"] = source  # 契约来源路径

    # 返回字段顺序保持构造顺序，便于人工阅读生成的 JSON。
    return dict_contract

# 目录扫描入口为接口契约检查批量收集 reference vector 摘要。
def find_vector_contracts(root: Path) -> list[dict[str, Any]]:
    """扫描目录下的 reference vector 文件并生成契约列表。

    参数:
        root: 需要递归扫描的根目录。

    返回:
        每个有效 `*vectors.json` 文件对应一个契约字典，无法读取的文件会被跳过。
    """

    # 汇总扫描到的有效契约，顺序跟文件路径排序保持一致。
    list_contracts: list[dict[str, Any]] = []  # 有效契约列表

    # 路径排序让报告顺序在不同文件系统上保持稳定。
    for vectors_file in sorted(root.glob("**/*vectors.json")):

        # 单个坏文件不应阻断整个工程的其它向量契约收集。
        try:

            # 复用单文件入口，确保扫描路径和 CLI 路径的契约一致。
            dict_contract = audit_vectors(vectors_file)  # 当前文件契约

        # 坏向量文件不进入契约列表，由后续验证阶段报告具体读取问题。
        except Exception:

            # 调用方只关心可用契约，坏文件由后续验证阶段单独报告。
            continue

        # path 字段使用相对 POSIX 形式，避免本机绝对路径进入报告。
        dict_contract["path"] = vectors_file.relative_to(root).as_posix()  # 相对向量路径

        # 保留当前文件的契约，供接口契约检查统一比对。
        list_contracts.append(dict_contract)

    # 返回全部有效契约，空目录自然得到空列表。
    return list_contracts

# 文本扫描入口从提示词或日志中恢复已声明的向量摘要。
def extract_vector_hashes(text: str) -> list[str]:
    """从文本中提取不重复的 reference vector 哈希。

    参数:
        text: 可能包含 `VECTOR_HASH_TAG` 标记的任意文本。

    返回:
        按首次出现顺序排列的哈希字符串列表。
    """

    # 用列表保留出现顺序，避免 set 打乱 transcript 中的证据顺序。
    list_hashes: list[str] = []  # 已发现哈希列表

    # 按行处理可以容忍标记出现在注释、日志或报告正文中。
    for text_line in text.splitlines():

        # 没有标记的行不含可提取的向量契约哈希。
        if VECTOR_HASH_TAG not in text_line:

            # 继续扫描后续行，保留跨段落提取能力。
            continue

        # 标记后的第一个空白分隔 token 即为嵌入的哈希值。
        str_hash_value = text_line.split(VECTOR_HASH_TAG, 1)[1].strip().split()[0]  # 当前行哈希

        # 只记录非空且首次出现的哈希，保持报告简洁。
        if str_hash_value and str_hash_value not in list_hashes:

            # 新哈希进入结果列表，后续重复值会被忽略。
            list_hashes.append(str_hash_value)

    # 返回按文本顺序去重后的哈希集合。
    return list_hashes

# 私有解析函数把历史输入形态统一成 case 列表。
def _cases_from_payload(payload: Any) -> list[Any]:
    """从兼容的 reference vector 负载中提取 case 列表。

    参数:
        payload: JSON 解码后的 list，或包含 cases/vectors 字段的 dict。

    返回:
        可继续规范化和哈希的原始 case 列表。

    异常:
        ValueError: 当负载无法解析出 list 形式的 case 序列时抛出。
    """

    # dict 负载兼容生成器历史上使用过的 cases 和 vectors 字段。
    if isinstance(payload, dict):

        # cases 优先级高于 vectors，避免同时存在时误读兼容字段。
        raw_cases: Any = payload.get("cases", payload.get("vectors", []))  # dict 负载解析出的用例序列

    # 非 dict 负载沿用历史约定，直接把自身视为 case 序列候选。
    else:

        # list 负载本身就是 reference vector 的 case 序列。
        raw_cases = payload  # 调用方直接传入的用例序列

    # 契约生成只接受 case 列表，防止对象或标量被悄悄哈希。
    if not isinstance(raw_cases, list):

        # 错误信息保持英文，延续 CLI/测试对外可读诊断的历史语义。
        raise ValueError("> ERR: [Python] Reference vectors must be a JSON list or an object with a cases list.")

    # 返回原始 case 列表，后续再做递归规范化。
    return raw_cases

# 私有规范化函数为哈希输入提供递归稳定排序。
def _normalize_json(json_value: Any) -> Any:
    """递归规范化 JSON 值以支持稳定哈希。

    参数:
        json_value: JSON 解码后得到的任意对象。

    返回:
        字典键稳定排序、列表语义顺序保留后的 JSON 兼容值。
    """

    # dict 键排序是稳定哈希的核心，所有键先转为字符串参与输出。
    if isinstance(json_value, dict):

        # 递归处理子值，确保嵌套对象也得到稳定顺序。
        return {str(raw_key): _normalize_json(json_value[raw_key]) for raw_key in sorted(json_value)}

    # list 保留原始顺序，因为向量内部序列顺序通常有语义。
    if isinstance(json_value, list):

        # 只规范化元素内容，不改变列表顺序。
        return [_normalize_json(item_value) for item_value in json_value]

    # 标量值直接返回，保持 JSON 解码后的原始语义。
    return json_value

# 私有排序键函数决定 case 列表的确定性顺序。
def _case_sort_key(vector_case: Any) -> str:
    """生成 reference vector case 的稳定排序键。

    参数:
        vector_case: 已经递归规范化的单个 case。

    返回:
        用于排序 case 列表的字符串键。
    """

    # dict case 优先使用人工指定的 id/name，方便报告顺序贴近设计意图。
    if isinstance(vector_case, dict):

        # 缺少 id/name 时用完整 JSON 表达式作为确定性排序键。
        return str(
            vector_case.get("id")
            or vector_case.get("name")
            or json.dumps(vector_case, sort_keys=True, ensure_ascii=False)
        )

    # 非 dict case 没有命名字段，只能使用稳定 JSON 文本排序。
    return json.dumps(vector_case, sort_keys=True, ensure_ascii=False)

# 私有标识函数为每个 case 生成报告和 transcript 共用的 id。
def _case_id(vector_case: Any, case_index: int) -> str:
    """生成报告和 transcript 共用的 case 标识。

    参数:
        vector_case: 已经递归规范化的单个 case。
        case_index: 当前 case 在排序后列表中的一基序号。

    返回:
        优先来自 id/name 字段的稳定 case 标识。
    """

    # dict case 可通过 id/name 保留作者在 fixtures 中写下的语义名称。
    if isinstance(vector_case, dict):

        # 缺少显式命名时用一基序号生成兼容的默认 case id。
        return str(vector_case.get("id") or vector_case.get("name") or f"case_{case_index}")

    # 非 dict case 只能使用序号，避免从结构内容生成冗长 id。
    return f"case_{case_index}"

# 私有字段收集函数提取输入或输出侧的可见键名。
def _keys_for(list_cases: list[Any], tuple_candidate_fields: tuple[str, ...]) -> list[str]:
    """收集 reference vector 输入或输出侧可见字段名。

    参数:
        list_cases: 已排序的 reference vector case 列表。
        tuple_candidate_fields: 需要按优先级检查的输入或输出字段名。

    返回:
        按首次出现顺序排列且去重后的字段名列表。
    """

    # 列表用于保持字段首次出现顺序，便于提示词和报告稳定展示。
    list_keys: list[str] = []  # 已收集字段名

    # 逐个 case 汇总输入或输出相关字段。
    for vector_case in list_cases:

        # 只有对象形式的 case 才可能包含命名输入输出字段。
        if not isinstance(vector_case, dict):

            # 非对象 case 无法提供字段名，直接跳过。
            continue

        # 按候选字段优先级扫描，兼容 inputs/input 与 outputs/expected/output。
        for field_name in tuple_candidate_fields:

            # 当前候选字段值决定是展开内部键，还是记录字段本身。
            field_payload = vector_case.get(field_name)  # 当前候选字段负载

            # dict 形式的 inputs/outputs 暴露实际端口或信号名。
            if isinstance(field_payload, dict):

                # 嵌套键的出现顺序来自规范化后的 case 字典。
                for nested_key in field_payload:

                    # 字段名在结果中只保留第一次出现的位置。
                    if str(nested_key) not in list_keys:

                        # 转成字符串后记录，匹配 JSON contract 的键类型。
                        list_keys.append(str(nested_key))

            # 候选字段直接存在时记录字段名本身，提醒上层存在标量或序列负载。
            elif field_name in vector_case and field_name not in list_keys:

                # 非 dict 字段仍记录候选字段名，提示调用方存在该类值。
                list_keys.append(field_name)

    # 返回按首次发现顺序排列的字段名。
    return list_keys

