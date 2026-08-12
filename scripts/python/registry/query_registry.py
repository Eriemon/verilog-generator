"""只读问询命令注册表；机器可读 stdout 协议在 JSON 模式输出单个对象。"""

# 延迟注解求值保持 CLI 与受支持 Python 版本兼容。
from __future__ import annotations

# 标准库依赖负责参数解析、JSON 解码、SQLite 查询和进程退出。
import argparse
import json
import re
import sqlite3
import sys

# 路径和通用映射类型用于技能根寻址与结果载荷。
from pathlib import Path
from typing import Any

# 共享模块提供注册源当前性门禁和稳定 JSON 编码。
from .registry_common import RegistryError

# 第二组共享依赖负责数据库打开和技能根解析。
from .registry_common import ensure_database_current, resolve_skill_root

# 问询长度上限避免无界文本进入 LIKE 和 FTS 查询。
INT_MAX_QUESTION_LENGTH = 1000  # 用户问询允许的最大字符数

# FTS 片段上限避免长问句生成无界 OR 表达式。
INT_MAX_FTS_TERMS = 64  # 单次自然语言补充召回允许的最多检索片段

# 请求异常与注册源异常使用不同退出码。
class RequestError(ValueError):
    """表示调用方参数不满足公开问询合同。"""

# 参数解析器公开唯一的只读 ask 子命令。
def parse_args(list_arguments: list[str] | None = None) -> argparse.Namespace:
    """解析问询 CLI 参数。

    参数：list_arguments 为可选测试参数；为空时读取真实命令行。
    返回：包含问询、分类、数量和输出模式的参数命名空间。
    """

    # 根解析器只负责选择公开动作。
    parser_root = argparse.ArgumentParser(  # 问询 CLI 根解析器
        description="Ask the local command registry for usage guidance."  # CLI 功能摘要
    )

    # 子解析器强制调用方明确使用 ask 动作。
    subparsers_actions = parser_root.add_subparsers(  # 公开动作解析器集合
        dest="action",  # 解析后的动作字段名
        required=True,  # 禁止省略动作名
    )

    # ask 解析器承载全部只读检索参数。
    parser_ask = subparsers_actions.add_parser(  # ask 子命令解析器
        "ask",  # 公开只读问询动作
        help="Retrieve ranked command guidance.",  # ask 动作帮助文本
    )

    # 自然语言问询是唯一必需位置参数。
    parser_ask.add_argument("question")

    # 分类过滤允许调用方缩小候选命令集合。
    parser_ask.add_argument("--category")

    # kind 选择命令、工作流、文档职责或知识指针命名空间。
    parser_ask.add_argument(
        "--kind",
        choices=("command", "workflow", "document", "knowledge"),
        default="command",
    )

    # Top-K 数量由请求门禁限制在小范围内。
    parser_ask.add_argument("--limit", type=int, default=5)

    # JSON 模式用于脚本消费稳定结果协议。
    parser_ask.add_argument("--json", action="store_true", dest="json_output")

    # 返回解析后的真实或测试参数。
    return parser_root.parse_args(list_arguments)

# 请求门禁在打开数据库前拒绝无效或无界输入。
def validate_request(
    str_question: str,
    str_category: str | None,
    int_limit: int,
    str_kind: str = "command",
) -> None:
    """校验问询文本、分类格式和 Top-K 范围。

    参数：str_question 为问询文本；str_category 为可选分类；int_limit 为结果上限；str_kind 为记录类型。
    返回：无业务返回值，参数合法时直接结束。
    异常：文本、分类或结果上限无效时抛出 RequestError。
    """

    # 纯空白问询不能形成有效检索意图。
    if not str_question.strip():

        # 请求错误不应触发数据库诊断。
        raise RequestError("> ERR: [Python] question must not be empty")

    # 有界问询防止超长输入消耗不必要的检索资源。
    if len(str_question) > INT_MAX_QUESTION_LENGTH:

        # 诊断公开固定长度上限供调用方修正。
        raise RequestError("> ERR: [Python] question must not exceed 1000 characters")

    # 结果数量保持在文档问询所需的小型 Top-K 范围。
    if not 1 <= int_limit <= 10:

        # 超界数量作为请求错误返回。
        raise RequestError("> ERR: [Python] limit must be between 1 and 10")

    # 分类只接受字母、数字和连字符，避免动态 SQL 语义混入。
    if str_category is not None and not str_category.replace("-", "").isalnum():

        # 分类始终通过参数绑定，但仍限制公开标识格式。
        raise RequestError("> ERR: [Python] category contains unsupported characters")

    # 分类字段只属于命令记录，其他命名空间不得静默忽略。
    if str_category is not None and str_kind != "command":

        # 调用方应移除分类或改为命令查询。
        raise RequestError("> ERR: [Python] --category is only valid for --kind command")

# LIKE 转义器保证用户通配符只按字面量参与召回。
def escape_like_text(str_value: str) -> str:
    """转义 SQLite LIKE 通配符。

    参数：str_value 为已规范化的问询文本。
    返回：仅按字面量参与 LIKE 匹配的文本。
    """

    # 先转义逃逸符本身，再转义两个 SQL 通配符。
    return str_value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

# 检索片段限制器在保持顺序的同时覆盖长问句首尾。
def limit_unique_terms(list_candidate_terms: list[str]) -> list[str]:
    """去重并限制自然语言检索片段。

    参数：list_candidate_terms 为按问句顺序提取的候选片段。
    返回：覆盖原始问句范围且不超过固定上限的唯一片段。
    """

    # 稳定去重防止重复概念放大同一记录的匹配权重。
    list_unique_terms = list(dict.fromkeys(list_candidate_terms))  # 按首次出现顺序去重的片段

    # 短问句无需抽样即可完整保留。
    if len(list_unique_terms) <= INT_MAX_FTS_TERMS:

        # 未触及上限时直接保留全部唯一片段。
        return list_unique_terms

    # 抽样比例把首尾候选都映射到固定数量的槽位。
    float_sample_scale = (len(list_unique_terms) - 1) / (INT_MAX_FTS_TERMS - 1)  # 全范围抽样步长

    # 四舍五入后的索引覆盖候选序列首尾和中间区域。
    return [  # 有界且覆盖全问句的检索片段
        list_unique_terms[round(int_index * float_sample_scale)]  # 当前抽样槽位对应的候选
        for int_index in range(INT_MAX_FTS_TERMS)  # 固定数量的抽样槽位
    ]

# 双字概念提取器为 trigram 无连续命中时提供确定性补充评分。
def build_substring_terms(str_normalized_question: str) -> list[str]:
    """提取英文词和中文双字概念。

    参数：str_normalized_question 为已完成大小写归一化的问询。
    返回：可用于字面量包含评分的有界检索片段。
    """

    # 英文词保留整体，中文连续文本按双字窗口覆盖常见技术概念。
    list_text_chunks = re.findall(  # 问询中的英文词和中文连续片段
        r"[0-9a-z_-]{3,}|[\u3400-\u9fff]{2,}",  # 英文整词或至少两个中文字符
        str_normalized_question,  # 完成大小写归一化的原始问询
    )

    # 候选列表按原始出现次序积累英文词和中文窗口。
    list_candidate_terms: list[str] = []  # 按问句顺序生成的包含匹配候选

    # 每个片段转换为适合字面量包含判断的最小概念。
    for str_chunk in list_text_chunks:

        # ASCII 标识整体参与包含评分，避免拆散工具名称。
        if str_chunk.isascii():

            # 当前英文词加入稳定候选序列。
            list_candidate_terms.append(str_chunk)

            # 英文分支已经完成当前片段处理。
            continue

        # 双字窗口保留“远程”“验证”等 trigram 无法独立索引的概念。
        list_candidate_terms.extend(
            str_chunk[int_start:int_start + 2]
            for int_start in range(len(str_chunk) - 1)
        )

    # 共享限制器保证补充评分同样有固定资源上限。
    return limit_unique_terms(list_candidate_terms)

# 关系表补充排序器只在 FTS 无结果时处理已读出的本地注册记录。
def rank_rows_by_substring_terms(
    list_rows: list[tuple[str, str, str]],
    str_normalized_question: str,
    int_limit: int,
) -> list[tuple[str]]:
    """按自然问句中的双字概念重合度排序注册记录。

    参数：list_rows 为 payload、search_text、标识三元组；str_normalized_question 为问询；
    int_limit 为返回上限。
    返回：与 SQLite fetchall 载荷形状一致的 payload 单元素元组。
    """

    # 空片段不能形成可靠的补充召回。
    list_terms = build_substring_terms(str_normalized_question)  # 有界包含匹配片段

    # 无有效词项时保持无补充结果的确定性行为。
    if not list_terms:

        # 空列表与 SQLite 无命中结果保持同一合同。
        return []

    # 分数列表保留命中数量、稳定标识与原始 JSON 载荷。
    list_scored_rows: list[tuple[int, str, str]] = []  # 全部候选记录的补充召回分数

    # 每条本地记录按唯一概念的包含数量计分。
    for str_payload, str_search_text, str_identifier in list_rows:

        # 大小写归一化后的注册文本只用于只读包含判断。
        str_normalized_search_text = str_search_text.casefold()  # 当前记录的规范化检索文本

        # 命中计数从零开始逐一累积唯一概念。
        int_match_count = 0  # 当前记录覆盖的唯一概念数量

        # 每个概念最多贡献一次分数，避免词频改变排名。
        for str_term in list_terms:

            # 当前检索文本包含该概念时递增覆盖数量。
            if str_term in str_normalized_search_text:

                # 单个唯一概念只增加一个匹配点。
                int_match_count += 1  # 累加当前唯一概念命中

        # 分数与稳定标识共同形成确定性排序输入。
        list_scored_rows.append((int_match_count, str_identifier, str_payload))

    # 匹配列表只接收至少覆盖一个问句概念的候选。
    list_matched_rows: list[tuple[int, str, str]] = []  # 通过最低匹配条件的候选

    # 零命中记录不得通过补充阶段进入公开结果。
    for tuple_scored_row in list_scored_rows:

        # 正分记录才具有可解释的字面概念关联。
        if tuple_scored_row[0] > 0:

            # 保留完整分数元组供后续稳定排序。
            list_matched_rows.append(tuple_scored_row)

    # 同分记录按规范标识排序，保证跨运行结果稳定。
    list_matched_rows.sort(key=lambda tuple_row: (-tuple_row[0], tuple_row[1]))

    # 恢复既有载荷元组合同，避免改变公开解码路径。
    return [(str_payload,) for _, _, str_payload in list_matched_rows[:int_limit]]

# 自然语言 FTS 查询器把中英文问句拆成有界的可召回片段。
def build_fts_match_query(str_normalized_question: str) -> str:
    """构造适用于 trigram 索引的自然语言 MATCH 表达式。

    参数：str_normalized_question 为去除首尾空白并完成大小写归一化的问询。
    返回：由英文词或中文三字片段组成的有界 OR 查询。
    """

    # 正则只保留 trigram 能稳定索引的英文词和连续中文文本。
    list_text_chunks = re.findall(  # FTS 阶段的英文词和中文长片段
        r"[0-9a-z_-]{3,}|[\u3400-\u9fff]{3,}",  # 与当前 trigram 分词器兼容的字符范围
        str_normalized_question,  # 已完成大小写归一化的问询文本
    )

    # 原始片段列表保留问句中的出现顺序供稳定排序。
    list_candidate_terms: list[str] = []  # 尚未去重的 FTS 检索片段

    # 每个连续片段按语言特征转换为可独立命中的检索单元。
    for str_chunk in list_text_chunks:

        # ASCII 标识和英文词保留整体，避免拆分后失去工具名语义。
        if str_chunk.isascii():

            # 英文词直接进入 OR 查询候选。
            list_candidate_terms.append(str_chunk)

            # 当前英文词已经完成处理。
            continue

        # 中文连续文本使用滑动三字片段容忍自然问句中的连接成分。
        for int_start in range(len(str_chunk) - 2):

            # 三字窗口与 FTS5 trigram 的最小稳定命中单位一致。
            str_term = str_chunk[int_start:int_start + 3]  # 当前中文三字检索片段

            # 保留当前窗口供后续去重和有界抽样。
            list_candidate_terms.append(str_term)

    # 共享限制器稳定去重并对超长问句做全范围抽样。
    list_unique_terms = limit_unique_terms(list_candidate_terms)  # 有界唯一 FTS 片段

    # 没有可分词内容时保留旧有完整短语行为供标点类边界诊断。
    if not list_unique_terms:

        # 双引号转义防止用户文本改变 FTS 短语边界。
        return '"' + str_normalized_question.replace('"', '""') + '"'

    # 每个片段独立转义并以 OR 连接，让 bm25 按实际重合度排序。
    return " OR ".join(  # 最终参数化 MATCH 查询文本
        '"' + str_term.replace('"', '""') + '"'  # 单个字面检索片段
        for str_term in list_unique_terms  # 已去重并限制数量的片段
    )

# 命令召回器先做确定性子串匹配，再使用 FTS5 补充召回。
def retrieve_commands(
    connection_database: sqlite3.Connection,
    str_question: str,
    str_category: str | None,
    int_limit: int,
) -> list[dict[str, Any]]:
    """召回并排序命令记录。

    参数：connection_database 为只读连接；str_question 为问询；str_category 为过滤分类；int_limit 为上限。
    返回：按匹配优先级排序的命令 JSON 记录。
    异常：SQLite 查询失败时向调用方传播 sqlite3.Error。
    """

    # 大小写归一化让英文标识和别名匹配保持一致。
    str_normalized_question = str_question.strip().casefold()  # 规范化问询文本

    # 转义后文本不能把百分号或下划线解释为通配符。
    str_like_question = escape_like_text(str_normalized_question)  # 字面量 LIKE 问询

    # 首个参数对应 search_text 的包含匹配。
    list_query_parameters: list[object] = [f"%{str_like_question}%"]  # LIKE 查询参数

    # 分类片段只在显式过滤时启用。
    str_category_clause = ""  # 可选分类 SQL 片段

    # 分类值始终作为参数传入，禁止拼接用户输入。
    if str_category:

        # SQL 片段仅包含固定列名和占位符。
        str_category_clause = " AND category = ?"  # 第一阶段分类过滤片段

        # 追加分类参数保持占位符顺序一致。
        list_query_parameters.append(str_category)

    # 查询末尾参数限制返回行数。
    list_query_parameters.append(int_limit)

    # 子串命中优先，并让命令标识直接命中排在前面。
    list_rows = connection_database.execute(  # 第一阶段召回行
        "SELECT payload_json FROM commands "
        "WHERE lower(search_text) LIKE ? ESCAPE '\\'" + str_category_clause +
        " ORDER BY CASE WHEN lower(command_id) LIKE '%' || ? || '%' ESCAPE '\\' "
        "THEN 0 ELSE 1 END, command_id LIMIT ?",
        [*list_query_parameters[:-1], str_like_question, list_query_parameters[-1]],  # 完整绑定参数
    ).fetchall()

    # 无子串结果且文本足够长时启用 trigram FTS 补充召回。
    if not list_rows and len(str_normalized_question) >= 3:

        # 片段化查询允许自然问句包含未登记的连接词。
        str_fts_query = build_fts_match_query(str_normalized_question)  # 有界 FTS MATCH 参数

        # 短语参数直接绑定 MATCH 占位符，保留 trigram 查询边界。
        list_fts_parameters: list[object] = [str_fts_query]  # 承载短语与过滤条件的参数

        # FTS 分类片段使用 commands 表限定列名。
        str_fts_category_clause = ""  # 未指定分类时的空过滤片段

        # 分类过滤在两阶段召回中保持相同语义。
        if str_category:

            # 固定 SQL 片段不包含用户文本。
            str_fts_category_clause = " AND commands.category = ?"  # FTS 分类过滤片段

            # 分类参数按占位符顺序追加。
            list_fts_parameters.append(str_category)

        # FTS 同样严格遵循 Top-K 上限。
        list_fts_parameters.append(int_limit)

        # 第二阶段仅在本地数据库中按 bm25 分数和命令标识排序。
        list_rows = connection_database.execute(  # 第二阶段召回行
            "SELECT commands.payload_json FROM command_fts "
            "JOIN commands ON commands.command_id = command_fts.command_id "
            "WHERE command_fts MATCH ?" + str_fts_category_clause +
            " ORDER BY bm25(command_fts), commands.command_id LIMIT ?",  # 稳定 FTS 排序
            list_fts_parameters,  # FTS 短语、分类和数量参数
        ).fetchall()

    # trigram 无连续命中时，按双字概念重合度补充组合语义召回。
    if not list_rows:

        # 缺省补充查询不添加分类过滤或分类参数。
        str_fallback_category_clause = ""  # 补充阶段的可选分类片段

        # 空元组表示补充查询当前没有分类绑定参数。
        tuple_fallback_parameters: tuple[str | None, ...] = ()  # 补充阶段分类参数

        # 显式分类继续通过固定片段和绑定参数进入 SQL。
        if str_category:

            # 固定 WHERE 片段不拼接用户分类文本。
            str_fallback_category_clause = " WHERE category = ?"  # 启用的分类过滤片段

            # 单元素参数元组保持数据库绑定顺序。
            tuple_fallback_parameters = (str_category,)  # 启用的分类绑定参数

        # 查询文本由固定命令表合同和可选固定分类片段组成。
        str_fallback_query = (  # 补充阶段的命令候选 SQL
            "SELECT payload_json, search_text, command_id FROM commands"
            f"{str_fallback_category_clause}"
        )

        # 本地候选保留检索文本和稳定标识供共享排序器使用。
        list_fallback_rows = connection_database.execute(  # 本地命令记录候选
            str_fallback_query,  # 固定命令候选 SQL
            tuple_fallback_parameters,  # 可选分类绑定参数
        ).fetchall()

        # 双字概念评分只在前两阶段均为空时接管结果。
        list_rows = rank_rows_by_substring_terms(  # 命令补充召回行
            list_fallback_rows,  # 分类过滤后的命令候选
            str_normalized_question,  # 命令补充评分使用的规范化问询
            int_limit,  # 命令补充召回的 Top-K 上限
        )

    # 数据库载荷由构建器使用规范 JSON 写入。
    return [json.loads(str_payload) for (str_payload,) in list_rows]

# 非命令记录召回器按受信任 kind 映射固定表名和标识列。
def retrieve_typed_records(
    connection_database: sqlite3.Connection,
    str_question: str,
    str_kind: str,
    int_limit: int,
) -> list[dict[str, Any]]:
    """召回工作流、文档职责或知识指针记录。

    参数：connection_database 为只读连接；str_question 为问询；str_kind 为记录类型；int_limit 为上限。
    返回：按子串或 FTS 分数排序的完整 JSON 记录。
    异常：未知 kind 抛出 RequestError；SQLite 失败向上传播。
    """

    # argparse choices 之外仍保留函数级安全映射。
    dict_table_contracts = {  # 非命令 kind 到固定数据库表合同
        "workflow": ("workflows", "workflow_fts", "workflow_id"),  # 工作流表合同。
        "document": ("documents", "document_fts", "document_id"),  # 文档职责表合同。
        "knowledge": ("knowledge", "knowledge_fts", "knowledge_id"),  # 知识指针表合同。
    }

    # 未知类型不能进入 SQL 标识符拼接。
    if str_kind not in dict_table_contracts:

        # 请求错误明确列出不支持类型。
        raise RequestError(f"> ERR: [Python] unsupported registry kind: {str_kind}")

    # 表名和标识列只来自代码内固定映射。
    tuple_contract = dict_table_contracts[str_kind]  # 当前记录类型数据库合同

    # 单次解包保证三个 SQL 标识来自同一受信任合同。
    str_table_name, str_fts_table_name, str_identifier_column = tuple_contract  # 关系表、FTS 表和主键列。

    # 英文大小写归一化与命令召回保持一致。
    str_normalized_question = str_question.strip().casefold()  # 规范化类型问询文本

    # typed 表子串阶段需要独立处理用户通配符。
    str_like_question = escape_like_text(str_normalized_question)  # typed 关系表字面量查询

    # 第一阶段优先使用确定性子串召回。
    list_rows = connection_database.execute(  # 类型记录子串召回行
        f"SELECT payload_json FROM {str_table_name} "
        "WHERE lower(search_text) LIKE ? ESCAPE '\\' "
        f"ORDER BY {str_identifier_column} LIMIT ?",
        (f"%{str_like_question}%", int_limit),  # 字面量问询与 Top-K 上限。
    ).fetchall()

    # 无子串结果且问询至少三字符时使用 trigram FTS。
    if not list_rows and len(str_normalized_question) >= 3:

        # 与命令召回共享自然语言片段化和长度边界。
        str_fts_query = build_fts_match_query(str_normalized_question)  # typed 记录的自然语言 FTS 参数

        # 表名均来自固定映射，用户文本只通过参数绑定。
        list_rows = connection_database.execute(  # 类型记录 FTS 召回行
            f"SELECT {str_table_name}.payload_json FROM {str_fts_table_name} "
            f"JOIN {str_table_name} ON {str_table_name}.{str_identifier_column} = "
            f"{str_fts_table_name}.{str_identifier_column} "
            f"WHERE {str_fts_table_name} MATCH ? "
            f"ORDER BY bm25({str_fts_table_name}), {str_table_name}.{str_identifier_column} LIMIT ?",
            (str_fts_query, int_limit),  # FTS 短语与 Top-K 上限。
        ).fetchall()

    # typed 记录的最后阶段处理跨字段分布的双字中文概念。
    if not list_rows:

        # SQL 标识全部来自受信任 kind 映射，不包含用户输入。
        str_fallback_query = (  # typed 补充阶段的候选 SQL
            f"SELECT payload_json, search_text, {str_identifier_column} "
            f"FROM {str_table_name}"
        )

        # 固定表合同下的本地候选携带检索文本和规范标识。
        list_fallback_rows = connection_database.execute(  # 本地类型记录候选
            str_fallback_query  # 当前 typed 表的固定候选 SQL
        ).fetchall()

        # 共享评分器保持各 typed 命名空间的排序语义一致。
        list_rows = rank_rows_by_substring_terms(  # typed 补充召回行
            list_fallback_rows,  # 当前 typed 命名空间的全部本地候选
            str_normalized_question,  # typed 补充评分使用的规范化问询
            int_limit,  # typed 补充召回的 Top-K 上限
        )

    # 构建器写入的规范 JSON 恢复为公开记录。
    return [json.loads(str_payload) for (str_payload,) in list_rows]

# Python 调用入口复用 CLI 的请求门禁和只读数据库当前性检查。
def query_registry(
    path_skill_root: Path,
    str_question: str,
    *,
    kind: str = "command",
    category: str | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """只读查询当前技能注册表并返回完整 JSON 记录。

    参数：path_skill_root 为技能根目录；str_question 为问询文本；kind 为记录类型；
    category 为可选命令分类；limit 为返回数量上限。
    返回：按注册表检索顺序排列的完整记录，不执行任何召回命令。
    异常：请求无效时抛出 RequestError；索引缺失或过期时抛出 RegistryError。
    """

    # 函数调用与 CLI 使用同一请求合同，避免两个入口产生语义漂移。
    validate_request(str_question, category, limit, kind)

    # 当前性门禁以只读模式打开 SQLite，并拒绝陈旧索引。
    connection_database, _ = ensure_database_current(path_skill_root)  # 当前只读注册表连接

    # 无论召回成功还是失败，都必须释放只读连接。
    try:

        # 命令记录支持分类过滤并保留完整执行、输出和风险合同。
        if kind == "command":

            # 返回原始 JSON 记录，供程序化调用方读取全部注册语义。
            return retrieve_commands(connection_database, str_question, category, limit)

        # 其他命名空间使用固定表映射，不接受命令分类。
        return retrieve_typed_records(connection_database, str_question, kind, limit)

    # 连接生命周期由公开函数完整拥有。
    finally:

        # 关闭只读连接，不修改生成数据库。
        connection_database.close()

# 结果投影器只公开执行指导所需字段，不返回内部检索文本。
def result_projection(dict_command: dict[str, Any]) -> dict[str, Any]:
    """收窄单条命令结果。

    参数：dict_command 为数据库中的完整命令记录。
    返回：供问询消费者读取的执行指导字段映射。
    """

    # 投影保留执行参数、输出协议、关联导航、风险、边界和正反示例。
    return {
        "id": dict_command["id"],
        "category": dict_command["category"],
        "title": dict_command["title"],
        "summary": dict_command["summary"],
        "when_to_use": dict_command["when_to_use"],
        "parameters": dict_command["parameters"],
        "risk": dict_command["risk"],
        "invocation_templates": dict_command["invocation_templates"],
        "examples": dict_command["examples"],
        "prerequisites": dict_command["prerequisites"],
        "outputs": dict_command["outputs"],
        "boundaries": dict_command["boundaries"],
        "related_command_ids": dict_command["related_command_ids"],
    }

# JSON 输出器把所有机器模式收敛到单个直接 dump 调用。
def emit_json(dict_payload: dict[str, Any]) -> None:
    """输出单个机器可读 JSON 对象。

    参数：dict_payload 为成功、无结果或失败载荷。
    返回：无业务返回值，单行 JSON 写入 stdout。
    """

    # 尾随换行便于调用方按行读取唯一 JSON 对象。
    sys.stdout.write(json.dumps(dict_payload, ensure_ascii=False, sort_keys=True) + "\n")

# 人类输出器展示命令用途、风险和一个有效调用示例。
def emit_human_results(str_question: str, list_results: list[dict[str, Any]]) -> None:
    """输出终端可读的命令指导。

    参数：str_question 为原始问询；list_results 为已投影结果。
    返回：无业务返回值，内容写入标准输出。
    """

    # 首行声明当前问询，遵循仓库过程信息前缀。
    print(f"> INFO: [Python] registry query: {str_question}")

    # 每条结果输出稳定编号和最小执行信息。
    for int_index, dict_result in enumerate(list_results, start=1):

        # 风险映射用于展示写入属性和风险等级。
        dict_risk = dict_result["risk"]  # 当前结果风险信息

        # 标题单独读取，避免终端输出器处理完整结构化映射。
        str_title = str(dict_result["title"])  # 当前命令标题

        # 标识对应 JSON 注册源中的稳定记录。
        str_command_id = str(dict_result["id"])  # 当前命令注册标识

        # 摘要文本解释命令职责。
        str_summary = str(dict_result["summary"])  # 当前命令功能摘要

        # 有效示例只作为文本展示。
        str_example = str(dict_result["examples"]["valid"][0])  # 当前命令首个有效示例

        # 风险字段转换为短文本，避免输出器直接处理结构化映射。
        str_risk_level = str(dict_risk["level"])  # 当前命令风险等级

        # 写入标志使用小写文本保持终端协议稳定。
        str_writes = str(dict_risk["writes"]).lower()  # 当前命令是否写入

        # 标题和标识让调用方定位注册源记录。
        print(f"> INFO: [Python] result {int_index}: {str_title} ({str_command_id})")

        # 摘要解释命令职责。
        print(f"> INFO: [Python] summary: {str_summary}")

        # 风险行明确命令是否可能写入。
        print(f"> INFO: [Python] risk={str_risk_level}; writes={str_writes}")

        # 只展示注册的有效示例，不执行该字符串。
        print(f"> INFO: [Python] command: {str_example}")

# 类型记录输出器展示标题、摘要和权威 Markdown 指针。
def emit_human_typed_results(
    str_question: str,
    str_kind: str,
    list_results: list[dict[str, Any]],
) -> None:
    """输出工作流、文档或知识记录的终端摘要。

    参数：str_question 为原始问询；str_kind 为记录类型；list_results 为完整记录。
    返回：无业务返回值，内容写入标准输出。
    """

    # 首行声明当前类型问询。
    print(f"> INFO: [Python] registry {str_kind} query: {str_question}")

    # 每条记录只展示标识、标题、摘要和可用来源路径。
    for int_index, dict_result in enumerate(list_results, start=1):

        # 四类记录分别使用 id 或历史专用标识字段。
        str_record_id = str(  # 当前类型记录标识
            dict_result.get("id")  # JSON 源通用标识。
            or dict_result.get("workflow_id")  # 工作流数据库标识回退。
            or dict_result.get("document_id")  # 文档数据库标识回退。
            or dict_result.get("knowledge_id")  # 知识数据库标识回退。
            or "<unknown>"  # 缺失标识的防御占位符。
        )

        # 标题是所有 typed 源记录的首要展示字段。
        str_title = str(dict_result.get("title", str_record_id))  # 当前类型记录标题

        # 摘要缺失时回退到文档职责说明。
        str_summary = str(dict_result.get("summary", dict_result.get("responsibility", "")))  # 当前类型记录摘要

        # 来源优先使用知识指针路径，其次使用文档目录路径。
        str_source_path = str(dict_result.get("source_path", dict_result.get("path", "")))  # 当前权威来源路径

        # 标题行绑定稳定标识。
        print(f"> INFO: [Python] result {int_index}: {str_title} ({str_record_id})")

        # 非空摘要解释记录用途。
        if str_summary:

            # 摘要不包含完整 Markdown 正文。
            print(f"> INFO: [Python] summary: {str_summary}")

        # 文档和知识记录显示权威来源指针。
        if str_source_path:

            # 来源路径允许调用方继续读取权威 Markdown。
            print(f"> INFO: [Python] source: {str_source_path}")

# 主入口协调请求门禁、只读连接、召回和稳定退出码。
def main(list_arguments: list[str] | None = None) -> int:
    """执行只读注册表问询。

    参数：list_arguments 为可选测试参数；为空时读取真实命令行。
    返回：成功为 0，无结果为 1，请求错误为 2，索引错误为 3。
    异常：argparse 对语法错误保持标准 SystemExit；领域错误在函数内转换为退出码。
    """

    # 参数解析在任何数据库访问前完成。
    namespace_args = parse_args(list_arguments)  # 已解析问询参数

    # 请求错误独立映射到退出码 2。
    try:

        # 校验不会读取或修改注册表数据库。
        validate_request(
            namespace_args.question,
            namespace_args.category,
            namespace_args.limit,
            namespace_args.kind,
        )

    # 请求错误保留机器和人类两种稳定输出。
    except RequestError as object_error:

        # 错误载荷始终包含空结果列表。
        dict_error = {"ok": False, "error": str(object_error), "results": []}  # 请求错误载荷

        # JSON 模式使用明确的机器协议。
        if namespace_args.json_output:

            # 单对象载荷供上层代理解析。
            emit_json(dict_error)

        # 人类模式只显示前缀错误文本。
        else:

            # RequestError 已携带 Python 错误前缀。
            print(f"> ERR: [Python] registry request failed: {object_error}")

        # 退出码 2 仅表示调用参数无效。
        return 2

    # 默认技能根由当前脚本位置确定。
    path_skill_root = resolve_skill_root()  # 当前技能源码根目录

    # 数据库错误统一映射到退出码 3。
    try:

        # 当前性门禁返回只读 URI 连接。
        connection_database, _ = ensure_database_current(path_skill_root)  # 当前只读连接及忽略的元数据

        # 无论查询成功或失败都必须关闭连接。
        try:

            # 空列表承接数据库召回的完整命令载荷。
            list_retrieved_commands: list[dict[str, Any]] = []  # 保留 parameters、risk、examples 与调用模板的原始记录

            # command 保留分类过滤和既有投影合同。
            if namespace_args.kind == "command":

                # 召回只返回 JSON 指导，不执行任何命令模板。
                list_retrieved_commands.extend(
                    retrieve_commands(
                        connection_database,  # 已验证只读数据库连接
                        namespace_args.question,  # 原始问询文本
                        namespace_args.category,  # 可选分类过滤
                        namespace_args.limit,  # Top-K 数量
                    )
                )

            # 其他 kind 使用独立固定表召回。
            else:

                # 分类只属于 command，typed 记录不应用分类过滤。
                if namespace_args.category:

                    # 请求错误避免静默忽略分类参数。
                    raise RequestError("> ERR: [Python] --category is only valid for --kind command")

                # typed 结果保留完整职责或权威指针载荷。
                list_retrieved_commands.extend(
                    retrieve_typed_records(
                        connection_database,  # typed 查询使用的只读连接。
                        namespace_args.question,  # typed 查询原始问题。
                        namespace_args.kind,  # typed 记录命名空间。
                        namespace_args.limit,  # typed 查询 Top-K 上限。
                    )
                )

        # Windows 上重建数据库前必须释放查询句柄。
        finally:

            # 关闭本次只读数据库连接。
            connection_database.close()

    # 注册源漂移和 SQLite 查询错误都表示索引不可用。
    except (RegistryError, sqlite3.Error) as object_error:

        # 索引错误同样返回空结果列表。
        dict_error = {"ok": False, "error": str(object_error), "results": []}  # 索引错误载荷

        # JSON 模式保持单对象协议。
        if namespace_args.json_output:

            # 机器调用方获得可解析错误载荷。
            emit_json(dict_error)

        # 终端模式只显示简短 Python 错误。
        else:

            # 动态错误文本附加在固定前缀之后。
            print(f"> ERR: [Python] registry query failed: {object_error}")

        # 退出码 3 触发调用方检查或重建索引。
        return 3

    # 命令结果投影移除内部字段，其他记录本身已经是最小指针载荷。
    list_results = (  # 当前 kind 的公开结果列表
        [
            result_projection(dict_command)  # 裁剪单条命令内部字段。
            for dict_command in list_retrieved_commands  # 遍历完整命令记录。
        ]
        if namespace_args.kind == "command"  # 命令记录使用安全裁剪投影。
        else list_retrieved_commands  # typed 源本身已经是最小注册载荷。
    )

    # 成功载荷中的 ok 与结果是否非空严格一致。
    dict_payload = {  # 问询成功或无结果载荷
        "ok": bool(list_results),  # 是否存在公开结果
        "query": namespace_args.question,  # 原始用户问询
        "kind": namespace_args.kind,  # 当前查询记录命名空间
        "results": list_results,  # 已裁剪的公开命令指导
    }

    # 机器模式始终输出单个 JSON 对象。
    if namespace_args.json_output:

        # emit_json 固定键顺序并保留中文。
        emit_json(dict_payload)

    # 人类模式有结果时逐条输出指导。
    elif list_results and namespace_args.kind == "command":

        # 人类输出同样只展示而不执行命令。
        emit_human_results(namespace_args.question, list_results)

    # 非命令记录使用最小指针输出器。
    elif list_results:

        # typed 输出不假设 risk 或 invocation_templates 字段存在。
        emit_human_typed_results(namespace_args.question, namespace_args.kind, list_results)

    # 无结果使用警告前缀并返回退出码 1。
    else:

        # 原始问询用于帮助调用方调整关键词。
        print(f"> WARNING: [Python] no matching command guidance for: {namespace_args.question}")

    # 结果非空才表示问询成功。
    return 0 if list_results else 1

# 直接执行脚本时启动只读问询 CLI。
if __name__ == "__main__":

    # 将稳定业务退出码交给调用进程。
    sys.exit(main())
