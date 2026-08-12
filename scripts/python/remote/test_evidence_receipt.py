"""构建绑定当前 Git 树和远程阶段证据的 release-gate 测试收据。"""

# 这个产品模块虽然以 test_ 开头，但不是 pytest 测试文件。
__test__ = False  # 禁止 pytest 把收据构建函数当作测试用例

# datetime 负责验证远程阶段时间戳的存在和时区语义。
from datetime import datetime

# hashlib/json 实现跨平台 canonical 收据和源码清单摘要。
import hashlib
import json

# subprocess 只通过参数列表查询 Git，不允许 shell 拼接。
import subprocess

# Path 负责项目、远程证据和收据输出路径的边界检查。
from pathlib import Path

# Any 仅用于远程 JSON 和收据对象的异构字段。
from typing import Any

# agents-md-generator release validator 共享这组非测试源码排除边界。
SOURCE_MANIFEST_EXCLUDES = (  # 非测试发布清单的固定排除边界
    ":(exclude,glob)AGENTS.md",  # 根规则属于治理元数据，不是运行时技能源码
    ":(exclude,glob)**/AGENTS.md",  # 作用域规则同样不进入发布源码哈希
    ":(exclude,glob)tests/**",  # TESTER 独占测试树
    ":(exclude,glob)docs/git_manager/test-evidence-*.json",  # 所有发布收据均不进入自身清单
    ":(exclude,glob).agents/semantic-review-*.json",  # 临时语义审查证据
    ":(exclude,glob).settings/**",  # 本地运行配置
    ":(exclude,glob).codebase-memory/**",  # 图谱持久化产物
    ":(exclude,glob)dist/**",  # 版本化发布产物
    ":(exclude,glob)docs/handoff/**",  # 收尾交接历史
    ":(exclude,glob)docs/memory/**",  # 项目记忆运行态
    ":(exclude,glob)**/history/**",  # 通用历史目录
    ":(exclude,glob)**/archive/**",  # 通用归档目录
)

# release validator 要求三个固定远程 pytest 阶段。
REMOTE_PHASES = ("targeted", "regression", "full")  # 收据绑定的远程验证阶段顺序

# 测试树内容摘要排除 Python 缓存，但 release gate 主绑定仍是 Git tree id。
TEST_TREE_EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo"})  # 不纳入测试内容事实的缓存后缀

# canonical_receipt_sha256 固定收据自哈希的字节合同。
def canonical_receipt_sha256(dict_receipt: dict[str, Any]) -> str:
    """计算排除 ``receipt_sha256`` 后的 canonical 收据摘要。

    :param dict_receipt: 待重算的收据对象。
    :return: 排除自身字段后的 UTF-8 JSON SHA-256 十六进制摘要。
    """

    # 浅复制足以移除自引用字段，不改变调用方原始对象。
    dict_payload = dict(dict_receipt)  # 排除自哈希字段后的收据副本

    # 自哈希值本身不得进入摘要输入。
    dict_payload.pop("receipt_sha256", None)

    # 与 release validator 保持 ensure_ascii、键序和分隔符合同一致。
    bytes_canonical = json.dumps(  # canonical 收据 JSON 的 UTF-8 字节
        dict_payload,  # 排除自身字段后的对象
        ensure_ascii=False,  # 保留远程标识中的 UTF-8 语义
        sort_keys=True,  # 固定对象键顺序
        separators=(",", ":"),  # 去除非语义空白
    ).encode("utf-8")

    # 返回值供 builder 写入 receipt_sha256。
    return hashlib.sha256(bytes_canonical).hexdigest()

# _run_git 统一执行没有 shell 的只读 Git 查询。
def _run_git(path_project: Path, list_args: list[str]) -> bytes:
    """执行一个只读 Git 查询并返回原始 stdout。

    :param path_project: Git 仓库根目录。
    :param list_args: 不含 git 前缀的参数列表。
    :return: Git 标准输出原始字节。
    :raises RuntimeError: Git 查询退出码非零时抛出。
    """

    # 不经 shell 保持路径和 Git pathspec 的边界可审计。
    completed_process = subprocess.run(  # 当前 Git 查询的进程结果
        ["git", *list_args],  # Git 参数列表
        cwd=path_project,  # 仓库根作为查询工作目录
        check=False,  # 由本函数统一检查退出码
        capture_output=True,  # 禁止诊断文本污染机器协议
    )

    # 任一 Git 失败都不能生成看似完整的收据。
    if completed_process.returncode != 0:

        # 错误文本不回显路径或仓库内部内容。
        raise RuntimeError("> ERR: [Python] Git evidence query failed")

    # 返回原始 stdout 供不同证据函数按各自合同解析。
    return completed_process.stdout

# _test_commit_is_ancestor 检查 TESTER 提交是否进入当前产品历史。
def _test_commit_is_ancestor(path_project: Path, str_test_commit_sha: str) -> bool:
    """判断 TESTER 提交是否为当前 HEAD 的祖先。

    :param path_project: Git 仓库根目录。
    :param str_test_commit_sha: TESTER 提交 SHA。
    :return: 提交存在且为 HEAD 祖先时返回 True。
    """

    # merge-base 的退出码直接表达拓扑关系，不读取路径内容。
    completed_process = subprocess.run(  # TESTER 提交拓扑查询结果
        ["git", "merge-base", "--is-ancestor", str_test_commit_sha, "HEAD"],  # 祖先检查参数
        cwd=path_project,  # 当前源仓库根
        check=False,  # 由返回码决定是否可绑定
        capture_output=True,  # 不把 Git 诊断写入终端
    )

    # 只有精确成功状态才能作为收据提交绑定。
    return completed_process.returncode == 0

# tests_tree_hash 只暴露当前提交的 Git tests tree 身份。
def tests_tree_hash(path_project: Path) -> str:
    """读取当前提交的 ``HEAD:tests`` Git tree id。

    :param path_project: Git 仓库根目录。
    :return: 当前提交中 tests 树的 Git tree id。
    :raises RuntimeError: 当前提交没有可解析的 tests 树时抛出。
    """

    # tests 内容对发布门禁保持不透明，只暴露 Git tree 身份。
    bytes_tree_hash = _run_git(path_project, ["rev-parse", "HEAD:tests"])  # 当前 tests Git tree 原始输出

    # Git object id 使用 ASCII 十六进制文本。
    return bytes_tree_hash.decode("ascii").strip()

# source_manifest_sha256 复刻 release validator 的非测试源码清单算法。
def source_manifest_sha256(path_project: Path) -> str:
    """按 release validator 算法计算当前非测试源码清单摘要。

    :param path_project: Git 仓库根目录。
    :return: 按路径、内容 SHA-256 和字节数绑定的清单摘要。
    :raises RuntimeError: Git 查询失败或候选不是普通文件时抛出。
    """

    # Git 先排除 tests、运行态和历史目录，避免收据自引用或泄漏测试成员。
    bytes_paths = _run_git(  # 已应用发布排除边界的路径清单
        path_project,  # 当前仓库根目录用于解析发布清单
        [
            "ls-files",  # 使用 Git 索引与工作树候选查询
            "--cached",  # 纳入已跟踪文件
            "--others",  # 纳入未跟踪产品候选
            "--exclude-standard",  # 应用仓库忽略规则
            "-z",  # 使用无歧义 NUL 分隔
            "--",  # 终止 Git 选项解析
            ".",  # 查询当前项目全域
            *SOURCE_MANIFEST_EXCLUDES,  # 应用固定排除边界
        ],
    )

    # 空成员来自终止 NUL，不进入路径集合。
    list_relative_bytes = sorted(  # 稳定的仓库相对路径字节集合
        bytes_relative  # 当前候选的相对路径字节
        for bytes_relative in bytes_paths.split(b"\0")  # 遍历 NUL 分隔路径
        if bytes_relative  # 排除终止 NUL 产生的空成员
    )

    # 整体摘要逐成员吸收无歧义的 NUL 分隔事实。
    hash_manifest = hashlib.sha256()  # 非测试源码清单聚合摘要

    # 每个候选只在 Git 完成排除后才读取内容。
    for bytes_relative in list_relative_bytes:

        # Git -z 路径使用 UTF-8；surrogateescape 保留异常字节的可逆语义。
        str_relative = bytes_relative.decode("utf-8", errors="surrogateescape")  # 当前仓库相对路径

        # 路径锚定仓库根，候选不存在时让调用方 fail closed。
        path_source = path_project / str_relative  # 当前非测试源码文件

        # 只允许普通文件进入发布清单。
        if not path_source.is_file():

            # Git 候选与工作树不一致时不能签发稳定收据。
            raise RuntimeError("> ERR: [Python] source manifest candidate is not a regular file")

        # 单文件字节同时提供内容摘要与精确规模。
        bytes_source = path_source.read_bytes()  # 当前源码原始字节

        # 单文件摘要绑定未经规范化的原始内容。
        str_source_sha256 = hashlib.sha256(bytes_source).hexdigest()  # 当前源码内容摘要

        # 路径、内容摘要和大小以 NUL 分隔，避免串联歧义。
        bytes_manifest_record = b"\0".join(  # 当前文件的无歧义清单记录
            (
                bytes_relative,  # 仓库相对路径字节
                str_source_sha256.encode("ascii"),  # 文件内容 SHA-256
                str(len(bytes_source)).encode("ascii"),  # 文件原始字节数
            )
        ) + b"\n"

        # 聚合摘要按稳定路径顺序吸收当前记录。
        hash_manifest.update(bytes_manifest_record)

    # 十六进制摘要供 TESTER 收据和发布门禁共享。
    return hash_manifest.hexdigest()

# tests_tree_content_facts 提供不透明 Git tree 之外的规模事实。
def tests_tree_content_facts(path_project: Path) -> dict[str, Any]:
    """计算测试树的稳定文件数、字节数和 framed 内容摘要。

    :param path_project: Git 仓库根目录。
    :return: 测试文件数、原始字节数和路径/内容 framed 摘要。
    """

    # 只有测试树内容事实在这里展开；发布 validator 仍只比较 Git tree id。
    path_tests = path_project / "tests"  # 当前测试树目录

    # 排序后的普通文件集合用于跨平台确定性计数。
    list_files = sorted(  # 测试树中的可审阅文件
        path  # 当前测试文件路径候选
        for path in path_tests.rglob("*")  # 递归枚举测试树候选
        if path.is_file()  # 只保留普通文件
        and "__pycache__" not in path.parts  # 排除解释器缓存目录
        and path.suffix not in TEST_TREE_EXCLUDED_SUFFIXES  # 排除 Python 字节码缓存
    )

    # framed 摘要显式编码路径与内容长度，避免边界串联歧义。
    hash_tree = hashlib.sha256()  # 测试树内容聚合摘要

    # 字节计数同时绑定所有保留文件的原始大小。
    int_byte_count = 0  # 测试树原始字节总数

    # 按稳定路径顺序吸收每个测试文件的路径和内容。
    for path_file in list_files:

        # 路径使用项目根相对 POSIX 形式。
        bytes_relative = path_file.relative_to(path_project).as_posix().encode("utf-8")  # 测试文件相对路径

        # 读取原始字节，禁止文本换行或编码规范化改变摘要。
        bytes_content = path_file.read_bytes()  # 测试文件原始字节

        # 先写路径长度和路径，再写内容长度和内容。
        # 先写相对路径长度，保证不同路径不会产生串联歧义。
        hash_tree.update(len(bytes_relative).to_bytes(8, "big"))

        # 再写路径本身，绑定文件在测试树中的位置。
        hash_tree.update(bytes_relative)

        # 写入内容长度，区分相邻文件的边界。
        hash_tree.update(len(bytes_content).to_bytes(8, "big"))

        # 最后吸收原始内容，形成可复算的测试树摘要。
        hash_tree.update(bytes_content)

        # 累计测试树原始字节规模。
        int_byte_count += len(bytes_content)  # 累加当前测试文件的原始字节数

    # 返回规模和内容事实；不公开测试文件成员。
    return {  # 测试树不透明规模摘要
        "tests_file_count": len(list_files),  # 测试普通文件数量
        "tests_byte_count": int_byte_count,  # 测试文件原始字节总数
        "tests_content_hash": hash_tree.hexdigest(),  # framed 内容摘要
    }

# _test_commit_outside_tests_count 验证 TESTER 提交没有越出 tests 边界。
def _test_commit_outside_tests_count(path_project: Path, str_test_commit_sha: str) -> int:
    """统计 TESTER 提交中 tests 边界外的变更文件数。

    :param path_project: Git 仓库根目录。
    :param str_test_commit_sha: TESTER 提交 SHA。
    :return: 不在 ``tests/`` 前缀下的变更文件数量。
    :raises RuntimeError: Git 提交查询失败时抛出。
    """

    # 只读取提交路径，不读取测试内容或提交消息。
    bytes_paths = _run_git(  # TESTER 提交的相对路径清单
        path_project,  # 当前仓库根目录用于查询 TESTER 提交
        ["diff-tree", "--no-commit-id", "--name-only", "-r", str_test_commit_sha],  # TESTER 提交路径查询参数
    )

    # Git 输出按行分隔；空行不是变更文件。
    list_paths = [item for item in bytes_paths.splitlines() if item]  # TESTER 提交路径集合

    # Windows 反斜杠归一化后仍必须落在 tests/ 前缀内。
    return sum(  # 越界变更文件数量
        1
        for item in list_paths
        if not item.replace(b"\\", b"/").startswith(b"tests/")
    )

# _require_timestamp 检查远程阶段时间戳的 ISO-8601 时区语义。
def _require_timestamp(dict_phase: dict[str, Any], str_phase: str) -> None:
    """确保阶段时间戳可解析且带有明确时区。

    :param dict_phase: 单阶段远程 pytest 证据。
    :param str_phase: 当前阶段名称。
    :return: 不返回业务值；非法时间直接抛出异常。
    :raises ValueError: 时间格式非法或缺少时区时抛出。
    """

    # Z 后缀转换成标准库可直接解析的显式 UTC 偏移。
    str_timestamp = str(dict_phase.get("timestamp", ""))  # 当前阶段时间戳文本

    # 时间解析失败时拒绝把证据标成新鲜。
    try:

        # ISO-8601 文本必须在进入 freshness 检查前转换成 datetime。
        datetime_value = datetime.fromisoformat(str_timestamp.replace("Z", "+00:00"))  # 当前阶段 datetime

    # ValueError 分支只负责把解析失败转成脱敏的阶段错误。
    except ValueError as exc:

        # 错误消息只暴露阶段名，便于机器定位而不泄漏路径。
        raise ValueError(f"> ERR: [Python] remote phase timestamp is invalid: {str_phase}") from exc

    # naive 时间无法证明远程证据的真实时区。
    if datetime_value.tzinfo is None:

        # 明确拒绝依赖本地时区猜测。
        raise ValueError(f"> ERR: [Python] remote phase timestamp has no timezone: {str_phase}")

# _validate_remote_evidence 检查远程总表的阶段和身份字段。
def _validate_remote_evidence(dict_evidence: dict[str, Any]) -> None:
    """验证远程 evidence 总表的三阶段和身份哈希字段。

    :param dict_evidence: 远程 runtime 写出的总表对象。
    :return: 不返回业务值；不满足合同的字段直接抛出异常。
    :raises ValueError: 缺少身份、阶段或覆盖字段时抛出。
    """

    # 所有身份哈希必须由远端实际生成，空值不能进入 release 收据。
    for str_field in (
        "remote_server_id",
        "remote_fingerprint_hash",
        "remote_cwd_hash",
        "validation_archive_hash",
        "skill_pressure_report_hash",
    ):

        # 当前字段为空时不允许用本地默认值补齐。
        if not str(dict_evidence.get(str_field, "")):

            # 字段名足以支持机器化 fail-closed 诊断。
            raise ValueError(f"> ERR: [Python] remote evidence field is missing: {str_field}")

    # remote_pytest 必须是按阶段组织的对象。
    dict_phases = dict_evidence.get("remote_pytest")  # 远程 pytest 阶段映射

    # 非字典结构不能安全抽取三阶段字段。
    if not isinstance(dict_phases, dict):

        # 远程阶段缺失时禁止构建不完整收据。
        raise ValueError("> ERR: [Python] remote pytest phase evidence is missing")

    # 三阶段逐一检查成功状态、覆盖数量、命令摘要和时间戳。
    for str_phase in REMOTE_PHASES:

        # 当前阶段必须存在对象载荷。
        dict_phase = dict_phases.get(str_phase)  # 当前远程 pytest 阶段

        # 缺失或非字典阶段直接阻断。
        if not isinstance(dict_phase, dict):

            # 阶段名用于定位缺口，不回显远程日志。
            raise ValueError(f"> ERR: [Python] remote pytest phase is missing: {str_phase}")

        # 只有退出码零且状态 passed 才能进入发布收据。
        if dict_phase.get("exit_code") != 0 or dict_phase.get("status") != "passed":

            # 失败阶段保留在远端 retained 目录，但不签发成功收据。
            raise ValueError(f"> ERR: [Python] remote pytest phase did not pass: {str_phase}")

        # count 必须为正并且命令摘要非空，避免空跑伪造覆盖。
        if int(dict_phase.get("count", 0)) <= 0 or not str(dict_phase.get("command_hash", "")):

            # 阶段身份不完整时要求重新执行远程 gate。
            raise ValueError(f"> ERR: [Python] remote pytest phase count/hash is invalid: {str_phase}")

        # 时间戳必须带时区，供 release validator 后续做 freshness 检查。
        _require_timestamp(dict_phase, str_phase)

# build_test_evidence_receipt 生成 release-gate 需要的不透明测试收据。
def build_test_evidence_receipt(
    path_project: Path,
    path_remote_evidence: Path,
    path_output: Path,
    test_commit_sha: str,
) -> dict[str, Any]:
    """构建并写出 release-gate 所需的不透明测试收据。

    :param path_project: 当前源仓库根目录。
    :param path_remote_evidence: 本地下载的远程测试总表路径。
    :param path_output: 项目内收据输出路径。
    :param test_commit_sha: TESTER 独立 tests/** 提交 SHA。
    :return: 与写入文件完全一致的收据对象。
    :raises FileNotFoundError: 远程总表不存在时抛出。
    :raises ValueError: 路径、提交、阶段或证据字段不满足合同。
    """

    # 所有路径解析到绝对路径，后续边界判断不依赖当前进程目录。
    path_project = path_project.resolve()  # 当前项目绝对根目录

    # 远程总表路径必须先归一化，再执行普通文件检查。
    path_remote_evidence = path_remote_evidence.resolve()  # 本地远程总表绝对路径

    # 收据输出路径单独归一化，供项目边界判断。
    path_output = path_output.resolve()  # 目标收据绝对路径

    # 远程证据必须是普通文件，收据只能写在项目内。
    if not path_remote_evidence.is_file():

        # 缺失总表时禁止使用旧运行或本地推导值替代。
        raise FileNotFoundError("> ERR: [Python] remote evidence JSON is unavailable")

    # 项目根本身不是输出文件的 parent，必须严格要求后代路径。
    if path_project not in path_output.parents:

        # 防止 CLI 输出参数把收据写到源仓库外部。
        raise ValueError("> ERR: [Python] receipt output must stay inside the project")

    # 读取远程总表并在本地重验所有阶段和身份字段。
    dict_evidence = json.loads(path_remote_evidence.read_text(encoding="utf-8"))  # 远程总表对象

    # JSON 根必须是对象，不能把数组或标量当作证据。
    if not isinstance(dict_evidence, dict):

        # 保持结构错误 fail closed。
        raise ValueError("> ERR: [Python] remote evidence JSON must be an object")

    # 复核远端写出的身份字段和三阶段成功状态。
    _validate_remote_evidence(dict_evidence)

    # TESTER 提交必须存在且已进入当前 HEAD 历史。
    if not test_commit_sha or not _test_commit_is_ancestor(path_project, test_commit_sha):

        # 非祖先或不存在的 SHA 不能绑定当前发布源码。
        raise ValueError("> ERR: [Python] TESTER commit is not an ancestor of HEAD")

    # 测试树内容事实和 Git tree 身份共同进入不透明收据。
    dict_tree_facts = tests_tree_content_facts(path_project)  # 当前 tests 树规模与 framed 摘要

    # 组装与 agents-md-generator validator 对齐的固定字段。
    dict_receipt: dict[str, Any] = {  # 发布门禁消费的不透明测试收据
        "schema": 1,  # 收据 schema 版本
        "kind": "opaque-test-evidence",  # 收据类型标识
        "test_commit_sha": test_commit_sha,  # TESTER 提交拓扑绑定
        "tests_tree_hash": tests_tree_hash(path_project),  # 当前 Git tests tree 身份
        **dict_tree_facts,  # 测试树规模附加事实
        "source_manifest_hash": source_manifest_sha256(path_project),  # 非测试源码清单摘要
        "remote_server_id": dict_evidence["remote_server_id"],  # 远程目标身份
        "remote_fingerprint_hash": dict_evidence["remote_fingerprint_hash"],  # 远程环境指纹
        "remote_cwd_hash": dict_evidence["remote_cwd_hash"],  # 远程工作目录指纹
        "validation_archive_hash": dict_evidence["validation_archive_hash"],  # 远程归档清单指纹
        "remote_pytest": {  # 三阶段最小远程 pytest 证据
            str_phase: {  # 当前阶段的收据字段
                key: dict_evidence["remote_pytest"][str_phase][key]  # 从总表提取已验证字段
                for key in ("command_hash", "exit_code", "count", "timestamp")  # release validator 字段集合
            }
            for str_phase in REMOTE_PHASES  # 按固定顺序绑定三阶段
        },
        "skill_pressure_report_hash": dict_evidence["skill_pressure_report_hash"],  # 压力报告指纹
        "tests_outside_staged_count": _test_commit_outside_tests_count(path_project, test_commit_sha),  # TESTER 越界计数
    }

    # 收据自哈希覆盖除自身外的全部字段。
    dict_receipt["receipt_sha256"] = canonical_receipt_sha256(dict_receipt)  # canonical 收据摘要

    # 父目录必须存在，落盘文本使用稳定可审阅格式。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # 返回对象与写入文件完全一致，便于 CLI 输出同一摘要。
    str_receipt_json = json.dumps(  # 收据正文
        dict_receipt,  # 待写入的完整收据对象
        ensure_ascii=False,  # 保留 UTF-8 远程标识
        indent=2,  # 便于 release 审阅
        sort_keys=True,  # 稳定正文键序
    )

    # 收据正文写入 UTF-8 并以换行结束。
    path_output.write_text(str_receipt_json + "\n", encoding="utf-8")

    # 返回写盘后的完整对象供调用方输出 hash。
    return dict_receipt
