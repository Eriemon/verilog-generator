"""检查或重建命令注册索引；机器可读 stdout 协议始终输出单个 JSON 对象。"""

# 延迟注解求值保持 CLI 与受支持 Python 版本兼容。
from __future__ import annotations

# 标准库依赖负责参数、原子替换、SQLite 和进程退出。
import argparse
import json
import os

# 数据库、进程、路径和通用映射类型支撑构建编排。
import sqlite3
import sys
from pathlib import Path
from typing import Any

# 注册表共享模块提供源加载、摘要、检索投影和数据库门禁。
from .registry_common import (
    INT_SCHEMA_VERSION,
    STR_FTS_TOKENIZER,
    RegistryError,
    canonical_json,
    database_path,
)

# 数据库当前性和注册源加载用于构建前后闭环验证。
from .registry_common import (
    ensure_database_current,
    load_document_records,
    load_registry,
)

# 第二组共享依赖负责检索投影、路径解析和源摘要。
from .registry_common import (
    record_search_text,
    registry_root,
    resolve_skill_root,
    source_digest,
)

# 参数解析器公开默认检查和显式写入两种模式。
def parse_args(list_arguments: list[str] | None = None) -> argparse.Namespace:
    """解析构建器命令行参数。

    参数：list_arguments 为可选测试参数；为空时读取真实命令行。
    返回：包含 skill_dir 和 write 的 argparse 命名空间。
    """

    # 构建器默认只检查生成数据库是否与 JSON 同步。
    object_parser = argparse.ArgumentParser(  # 命令注册索引参数解析器
        description="Check or rebuild the command registry SQLite index."  # CLI 功能摘要
    )

    # 可选技能根支持所有者仓库和临时测试夹具。
    object_parser.add_argument("skill_dir", nargs="?", type=Path)

    # 写入开关是唯一允许替换数据库的显式信号。
    object_parser.add_argument(
        "--write",
        action="store_true",
        help="Rebuild the generated SQLite registry from JSON sources.",
    )

    # 返回解析结果供主编排器选择模式。
    return object_parser.parse_args(list_arguments)

# JSON 输出器把机器协议直接写入标准输出，不混入人类日志前缀。
def emit_json(dict_payload: dict[str, Any]) -> None:
    """输出单个机器可读 JSON 对象。

    参数：dict_payload 为构建成功或失败载荷。
    返回：无业务返回值，单行 JSON 写入 stdout。
    """

    # 尾随换行便于命令行调用方按行读取单对象协议。
    sys.stdout.write(json.dumps(dict_payload, ensure_ascii=False, sort_keys=True) + "\n")

# 结构创建器一次性建立关系表和 trigram FTS5 表。
def create_schema(connection_database: sqlite3.Connection) -> None:
    """创建命令注册数据库结构。

    参数：connection_database 为指向新临时文件的 SQLite 连接。
    返回：无业务返回值；结构写入当前事务。
    """

    # 单批 SQL 保证结构版本内的表和索引同步创建。
    connection_database.executescript(
        """
        PRAGMA page_size = 4096;
        PRAGMA journal_mode = DELETE;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE commands (
            command_id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            search_text TEXT NOT NULL
        );
        CREATE TABLE workflows (
            workflow_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            search_text TEXT NOT NULL
        );
        CREATE TABLE documents (
            document_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            title TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            search_text TEXT NOT NULL
        );
        CREATE TABLE knowledge (
            knowledge_id TEXT PRIMARY KEY,
            source_path TEXT NOT NULL,
            title TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            search_text TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE command_fts USING fts5(
            command_id UNINDEXED,
            search_text,
            tokenize='trigram'
        );
        CREATE VIRTUAL TABLE workflow_fts USING fts5(
            workflow_id UNINDEXED,
            search_text,
            tokenize='trigram'
        );
        CREATE VIRTUAL TABLE document_fts USING fts5(
            document_id UNINDEXED,
            search_text,
            tokenize='trigram'
        );
        CREATE VIRTUAL TABLE knowledge_fts USING fts5(
            knowledge_id UNINDEXED,
            search_text,
            tokenize='trigram'
        );
        """
    )

# 命令写入器保持关系表和 FTS 表逐条一致。
def insert_commands(
    connection_database: sqlite3.Connection,
    list_commands: list[dict[str, Any]],
) -> None:
    """写入全部命令记录及其检索文本。

    参数：connection_database 为构建连接；list_commands 为已校验命令。
    返回：无业务返回值；数据保留在当前事务。
    """

    # 标识排序保证不同平台获得相同逻辑行顺序。
    for dict_command in sorted(list_commands, key=lambda dict_item: str(dict_item["id"])):

        # 完整载荷允许查询结果恢复执行指导和风险边界。
        str_payload = canonical_json(dict_command)  # 当前命令 JSON 载荷

        # 检索投影只包含稳定且有召回价值的文本。
        str_search_text = record_search_text(dict_command)  # 当前命令检索文本

        # 关系行保存过滤字段，同时保留完整 JSON。
        tuple_command_row = (  # commands 表当前插入行
            str(dict_command["id"]),  # 命令稳定标识
            str(dict_command["category"]),  # 分类过滤值
            str(dict_command["title"]),  # 人类可读标题
            str_payload,  # 完整注册记录
            str_search_text,  # FTS 检索文本副本
        )

        # 主表插入先建立命令事实。
        connection_database.execute("INSERT INTO commands VALUES (?, ?, ?, ?, ?)", tuple_command_row)

        # FTS 表使用相同标识和检索文本建立召回索引。
        connection_database.execute(
            "INSERT INTO command_fts(command_id, search_text) VALUES (?, ?)",
            (tuple_command_row[0], str_search_text),
        )

# 工作流写入器为跨命令问询保留独立检索空间。
def insert_workflows(
    connection_database: sqlite3.Connection,
    list_workflows: list[dict[str, Any]],
) -> None:
    """写入全部工作流记录及其检索文本。

    参数：connection_database 为构建连接；list_workflows 为已校验工作流。
    返回：无业务返回值；数据保留在当前事务。
    """

    # 标识排序稳定工作流的物理插入顺序。
    for dict_workflow in sorted(list_workflows, key=lambda dict_item: str(dict_item["id"])):

        # 完整工作流载荷保留步骤和边界。
        str_payload = canonical_json(dict_workflow)  # 当前工作流 JSON 载荷

        # 工作流标题、摘要和别名进入独立 FTS 文档。
        str_search_text = record_search_text(dict_workflow)  # 当前工作流检索文本

        # 标识字符串用于关系表和 FTS 表对齐。
        str_workflow_id = str(dict_workflow["id"])  # 当前工作流稳定标识

        # 主表保存可逆载荷和检索文本。
        connection_database.execute(
            "INSERT INTO workflows VALUES (?, ?, ?, ?)",
            (str_workflow_id, str(dict_workflow["title"]), str_payload, str_search_text),
        )

        # FTS 表与主表共享稳定工作流标识。
        connection_database.execute(
            "INSERT INTO workflow_fts(workflow_id, search_text) VALUES (?, ?)",
            (str_workflow_id, str_search_text),
        )

# 文档职责写入器建立路径、职责和摘要的检索投影。
def insert_documents(
    connection_database: sqlite3.Connection,
    list_documents: list[dict[str, Any]],
) -> None:
    """写入文档职责记录及其 FTS 文本。

    参数：connection_database 为构建连接；list_documents 为已复核文档记录。
    返回：无业务返回值；数据保留在当前事务。
    """

    # 标识排序保证物理写入顺序稳定。
    for dict_document in sorted(list_documents, key=lambda dict_item: str(dict_item["id"])):

        # 完整职责记录使用规范 JSON 保存。
        str_payload = canonical_json(dict_document)  # 当前文档职责 JSON 载荷

        # 路径、职责和摘要共同支持文档层问询。
        str_search_text = "\n".join(  # 当前文档职责检索文本
            (
                str(dict_document["id"]),  # 文档稳定标识
                str(dict_document["path"]),  # 权威 Markdown 路径
                str(dict_document["responsibility"]),  # 唯一职责说明
                str(dict_document["summary"]),  # 文档检索摘要
            )
        )

        # 文档关系行同时支持路径过滤、列表展示和完整载荷恢复。
        tuple_document_row = (  # documents 表的职责记录关系行。
            str(dict_document["id"]),  # 主键使用的文档标识。
            str(dict_document["path"]),  # 主表保存的 Markdown 路径。
            str(dict_document["summary"]),  # 结果列表展示标题。
            str_payload,  # 可逆职责 JSON。
            str_search_text,  # 文档 FTS 投影。
        )

        # 主表和 FTS 表共享稳定文档标识。
        connection_database.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?)", tuple_document_row)

        # FTS 行只保存主键和可检索文本。
        connection_database.execute(
            "INSERT INTO document_fts(document_id, search_text) VALUES (?, ?)",
            (tuple_document_row[0], str_search_text),
        )

# 知识写入器只索引摘要与权威 Markdown 指针，不复制正文。
def insert_knowledge(
    connection_database: sqlite3.Connection,
    list_knowledge: list[dict[str, Any]],
) -> None:
    """写入知识指针记录及其 FTS 文本。

    参数：connection_database 为构建连接；list_knowledge 为已复核知识记录。
    返回：无业务返回值；数据保留在当前事务。
    """

    # 标识排序保持可复现数据库内容。
    for dict_record in sorted(list_knowledge, key=lambda dict_item: str(dict_item["id"])):

        # 完整记录只包含标题、摘要、关键词和权威来源指针。
        str_payload = canonical_json(dict_record)  # 当前知识指针 JSON 载荷

        # 关键词展开后与标题、摘要、路径共同进入检索文本。
        str_keywords = " ".join(map(str, dict_record.get("keywords", [])))  # 当前知识关键词文本

        # 知识检索投影不包含 Markdown 正文。
        str_search_text = "\n".join(  # 当前知识检索文本
            (
                str(dict_record["id"]),  # 主键和标识检索词。
                str(dict_record["title"]),  # 标题检索词。
                str(dict_record["summary"]),  # 语义摘要检索词。
                str(dict_record["source_path"]),  # 来源路径检索词。
                str_keywords,  # Agent 复核关键词。
            )
        )

        # 知识关系行保存来源过滤、标题展示与完整指针恢复字段。
        tuple_knowledge_row = (  # 权威正文指针、标题与 FTS 投影的持久化行。
            str(dict_record["id"]),  # 主表与 FTS 表共享的知识记录稳定主键。
            str(dict_record["source_path"]),  # 权威 Markdown 来源的相对路径。
            str(dict_record["title"]),  # 知识查询结果显示的可读标题。
            str_payload,  # 供只读查询恢复完整指针的 JSON 载荷。
            str_search_text,  # 汇总摘要、来源与关键词的 FTS 检索文本。
        )

        # 主表与 FTS 表共享知识标识。
        connection_database.execute("INSERT INTO knowledge VALUES (?, ?, ?, ?, ?)", tuple_knowledge_row)

        # FTS 行不复制结构化载荷。
        connection_database.execute(
            "INSERT INTO knowledge_fts(knowledge_id, search_text) VALUES (?, ?)",
            (tuple_knowledge_row[0], str_search_text),
        )

# 状态构造器在写入和默认检查模式下复用同一事实口径。
def build_status_payload(path_skill_root: Path, *, bool_wrote: bool) -> dict[str, Any]:
    """构造当前索引的机器可读状态。

    参数：path_skill_root 为技能根；bool_wrote 表示本轮是否重建。
    返回：包含摘要、计数、schema 和数据库路径的状态映射。
    异常：索引不满足当前源合同时透传 RegistryError。
    """

    # 数据库门禁同时返回连接和已验证元数据。
    tuple_database_state = ensure_database_current(path_skill_root)  # 当前数据库连接与元数据

    # 状态读取完成后立即释放连接，避免阻塞后续替换。
    tuple_database_state[0].close()

    # 元数据映射用于构造类型明确的 JSON 字段。
    dict_metadata = tuple_database_state[1]  # 已验证数据库元数据

    # 输出同时公开源计数和数据库计数的相等事实。
    return {
        "ok": True,
        "wrote": bool_wrote,
        "database": str(database_path(path_skill_root)),
        "registry_root": str(registry_root(path_skill_root)),
        "schema_version": int(dict_metadata["schema_version"]),
        "source_sha256": dict_metadata["source_sha256"],
        "command_count": int(dict_metadata["command_count"]),
        "source_command_count": int(dict_metadata["command_count"]),
        "workflow_count": int(dict_metadata["workflow_count"]),
        "document_count": int(dict_metadata["document_count"]),
        "knowledge_count": int(dict_metadata["knowledge_count"]),
        "fts_tokenizer": dict_metadata["fts_tokenizer"],
    }

# 数据库构建器在同目录临时文件中完成全部事务后原子替换。
def write_database(path_skill_root: Path) -> dict[str, Any]:
    """从 JSON 注册源原子重建 SQLite 索引。

    参数：path_skill_root 为待构建技能源码根目录。
    返回：重建完成且再次通过门禁的状态映射。
    异常：文件系统或 SQLite 构建失败时透传原始异常。
    """

    # 加载器在任何数据库写入前完成 schema 和关系校验。
    tuple_registry = load_registry(path_skill_root)  # 已校验清单、命令和工作流

    # 文档职责和知识指针来自完成态文档注册源。
    tuple_document_records = load_document_records(path_skill_root, tuple_registry[0])  # 文档与知识记录

    # 正式目标路径来自固定注册表目录合同。
    path_target = database_path(path_skill_root)  # 正式 SQLite 数据库路径

    # 同目录临时文件使 os.replace 保持原子性。
    path_temporary = path_target.with_suffix(".sqlite3.tmp")  # 临时 SQLite 数据库路径

    # 注册目录可能在新技能夹具中尚未创建完整。
    path_target.parent.mkdir(parents=True, exist_ok=True)

    # 上次中断留下的临时文件不得参与本轮构建。
    if path_temporary.exists():

        # 删除的只是固定临时文件，不触碰 JSON 源或正式数据库。
        path_temporary.unlink()

    # 所有写入在临时数据库连接内完成。
    try:

        # 新文件连接确保不存在旧表或残留行。
        connection_database = sqlite3.connect(path_temporary)  # 临时数据库连接

        # schema 创建必须先于元数据和记录插入。
        create_schema(connection_database)

        # 元数据把生成数据库绑定到当前源摘要和记录计数。
        dict_metadata = {  # 待写入数据库的可验证元数据
            "schema_version": str(INT_SCHEMA_VERSION),  # 当前结构版本
            "source_sha256": source_digest(path_skill_root, tuple_registry[0]),  # 当前源摘要
            "command_count": str(len(tuple_registry[1])),  # 当前命令数量
            "workflow_count": str(len(tuple_registry[2])),  # 当前工作流数量
            "document_count": str(len(tuple_document_records[0])),  # 当前文档职责数量
            "knowledge_count": str(len(tuple_document_records[1])),  # 当前知识指针数量
            "fts_tokenizer": STR_FTS_TOKENIZER,  # 当前分词器
        }

        # 键排序稳定 metadata 表的插入顺序。
        connection_database.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            sorted(dict_metadata.items()),
        )

        # 命令记录进入主表和命令 FTS 表。
        insert_commands(connection_database, tuple_registry[1])

        # 工作流记录进入主表和工作流 FTS 表。
        insert_workflows(connection_database, tuple_registry[2])

        # 文档职责记录进入文档关系表和 FTS 表。
        insert_documents(connection_database, tuple_document_records[0])

        # 知识记录只索引权威 Markdown 指针与摘要。
        insert_knowledge(connection_database, tuple_document_records[1])

        # 提交确保所有表在替换前持久化。
        connection_database.commit()

        # VACUUM 规范页面布局并压缩临时数据库。
        connection_database.execute("VACUUM")

        # Windows 原子替换前必须关闭文件句柄。
        connection_database.close()

        # 只有完整构建成功后才替换正式数据库。
        os.replace(path_temporary, path_target)

    # 构建失败时清理临时工件并保留旧正式数据库。
    except (OSError, sqlite3.Error):

        # 仅在临时文件真实存在时执行清理。
        if path_temporary.exists():

            # 清理不完整数据库，避免下次构建误读。
            path_temporary.unlink()

        # 原始异常由 CLI 主函数转换为退出码 3。
        raise

    # 替换后重新走完整数据库门禁，证明新索引可用。
    return build_status_payload(path_skill_root, bool_wrote=True)

# CLI 编排器将检查、写入和错误映射到稳定 JSON 协议。
def main(list_arguments: list[str] | None = None) -> int:
    """执行索引检查或写入模式。

    参数：list_arguments 为可选测试参数；为空时读取真实命令行。
    返回：成功为 0；源、数据库或 FTS 错误为 3。
    """

    # 参数解析结果决定技能根和是否允许写入。
    namespace_args = parse_args(list_arguments)  # 构建器命令行参数

    # 默认技能根从当前脚本位置推导。
    path_skill_root = resolve_skill_root(namespace_args.skill_dir)  # 待检查技能根目录

    # 领域错误和底层构建错误统一形成单对象 JSON。
    try:

        # 写入模式原子重建，默认模式仅执行当前性门禁。
        dict_payload = (  # 构建器机器可读结果
            write_database(path_skill_root)  # 显式写入模式结果
            if namespace_args.write  # 调用方允许重建时选择写入路径
            else build_status_payload(path_skill_root, bool_wrote=False)  # 默认只读检查结果
        )

    # 注册源或当前性错误已经携带稳定错误前缀。
    except RegistryError as object_error:

        # CLI profile 允许单个 JSON 对象作为完整标准输出。
        emit_json({"ok": False, "error": str(object_error)})

        # 退出码 3 表示索引不可用或不兼容。
        return 3

    # 文件系统和 SQLite 构建错误同属索引不可用。
    except (OSError, sqlite3.Error) as object_error:

        # 错误载荷保留底层诊断并添加 Python 前缀。
        emit_json(
            {"ok": False, "error": f"> ERR: [Python] cannot build registry database: {object_error}"}
        )

        # 调用方可以用统一退出码触发重建诊断。
        return 3

    # 成功状态同样使用单个 JSON 对象输出。
    emit_json(dict_payload)

    # 构建或检查成功返回零。
    return 0

# 直接执行脚本时启动命令注册索引 CLI。
if __name__ == "__main__":

    # sys.exit 将主函数状态传递给 shell 和上层代理。
    sys.exit(main())
