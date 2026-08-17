"""在远程 retained smoke 目录内生成真实环境、阶段和归档证据。"""

# hashlib/json 负责稳定编码和每类远端证据的 SHA-256。
import hashlib
import json
import os
import platform
import shutil
import sys

# datetime 记录 Agent 对本轮远端事实的自动审核时间。
from datetime import datetime, timezone

# Path 负责 retained 目录内的证据边界。
from pathlib import Path

# 远程 pytest 阶段顺序与 release validator 保持一致。
REMOTE_PHASES = ("targeted", "regression", "full")  # 远程 pytest 阶段的固定顺序

# 身份文件排除在 archive manifest 外，避免相互引用和哈希循环。
IDENTITY_FILE_NAMES = frozenset(  # 不纳入归档清单的循环身份文件
    {
        "completion.json",  # 外层完成标记由独立身份字段绑定
        "remote_test_evidence.json",  # 总表本身不能参与自己的归档哈希
        "remote_environment.json",  # 环境指纹由总表字段单独绑定
        "remote_cwd.json",  # cwd 哈希由总表字段单独绑定
        "skill_pressure_report.json",  # 压力哈希由总表字段单独绑定
        "validation_archive_manifest.json",  # 清单哈希不能把自己递归纳入
    }
)

# _canonical_bytes 固定远端 JSON 哈希的编码合同。
def _canonical_bytes(dict_payload: dict[str, object]) -> bytes:
    """返回远端 JSON 哈希使用的 canonical UTF-8 字节。

    :param dict_payload: 待编码的远端证据对象。
    :return: 按固定键序和分隔符编码的 UTF-8 字节。
    """

    # 规范 JSON 字节必须忽略缩进和键插入顺序。
    bytes_canonical = json.dumps(  # 远端证据哈希的唯一字节来源
        dict_payload,  # 待编码的远端证据对象
        ensure_ascii=False,  # 保留中文语义字段的 UTF-8 字节
        sort_keys=True,  # 消除字典插入顺序差异
        separators=(",", ":"),  # 消除格式空白差异
    ).encode("utf-8")

    # 返回值供环境、cwd、压力和归档身份分别计算。
    return bytes_canonical

# _write_json 统一 retained 证据文件的写出格式。
def _write_json(path_output: Path, dict_payload: dict[str, object]) -> None:
    """以稳定格式写出一个 retained JSON 文件。

    :param path_output: retained smoke 目录内的目标 JSON 路径。
    :param dict_payload: 待写入的结构化证据对象。
    :return: 不返回业务值；目标文件写入完成后结束。
    """

    # 证据目录可能由远程 shell 新建，也可能是单元 fixture 临时目录。
    path_output.parent.mkdir(parents=True, exist_ok=True)

    # pretty JSON 便于报告下载后人工复核，canonical 哈希另行计算。
    str_json = json.dumps(  # retained 文件正文
        dict_payload,  # retained 文件的结构化正文
        ensure_ascii=False,  # 以 UTF-8 写入中文字段
        indent=2,  # 保持人工审阅的层次
        sort_keys=True,  # 保持下载后字段顺序稳定
    )

    # 统一使用 UTF-8 和末尾换行，避免平台默认编码漂移。
    path_output.write_text(str_json + "\n", encoding="utf-8")

# _read_phase_payloads 只接受三阶段真实摘要，不从 aggregate 推导结果。
def _read_phase_payloads(path_smoke_root: Path) -> dict[str, dict[str, object]]:
    """读取并验证 targeted、regression、full 三个阶段摘要。

    :param path_smoke_root: 当前 retained smoke 运行目录。
    :return: 按固定阶段名称保存的远程 pytest 摘要。
    :raises FileNotFoundError: 任一阶段摘要不存在时抛出。
    :raises ValueError: 阶段名称、状态、计数或身份字段不完整时抛出。
    """

    # 逐阶段读取，确保缺失任一层都不能生成总证据。
    dict_phase_payloads: dict[str, dict[str, object]] = {}  # 已验证的三阶段摘要

    # 阶段顺序固定，receipt 和 archive 都依赖这个顺序。
    for str_phase in REMOTE_PHASES:

        # 阶段摘要文件名与远程 shell 的 runner 保持完全一致。
        path_phase = path_smoke_root / f"remote_pytest_{str_phase}_summary.json"  # 当前阶段摘要路径

        # JSON 解码失败说明远端阶段未形成结构化证据。
        dict_phase = json.loads(path_phase.read_text(encoding="utf-8"))  # 当前阶段原始摘要

        # 摘要必须是对象，不能把数组或标量当作成功证据。
        if not isinstance(dict_phase, dict):

            # 错误信息只暴露阶段名，不回显远端路径内容。
            raise ValueError(f"> ERR: [Python] remote pytest phase is not an object: {str_phase}")

        # 阶段字段必须和文件名一致，防止交叉覆盖。
        if dict_phase.get("phase") != str_phase:

            # 阶段身份不一致时立即 fail closed。
            raise ValueError(f"> ERR: [Python] remote pytest phase name mismatch: {str_phase}")

        # receipt 只接受退出码为零且状态为 passed 的阶段。
        if dict_phase.get("status") != "passed" or dict_phase.get("exit_code") != 0:

            # 失败阶段仍保留原始日志，但不能进入成功总表。
            raise ValueError(f"> ERR: [Python] remote pytest phase did not pass: {str_phase}")

        # count 必须是正数，避免空收集或全 deselected 被误认覆盖。
        if int(dict_phase.get("count", 0)) <= 0:

            # 没有执行用例时拒绝生成可发布证据。
            raise ValueError(f"> ERR: [Python] remote pytest phase count is invalid: {str_phase}")

        # 命令摘要和时间戳共同绑定本次阶段实际执行。
        if not str(dict_phase.get("command_hash", "")) or not str(dict_phase.get("timestamp", "")):

            # 缺少任一身份字段都不能形成新鲜远程证据。
            raise ValueError(f"> ERR: [Python] remote pytest phase identity is incomplete: {str_phase}")

        # 保存已通过结构检查的阶段载荷。
        dict_phase_payloads[str_phase] = dict_phase  # 保存当前阶段的完整原始载荷

    # 返回完整三阶段映射供总表和压力报告复用。
    return dict_phase_payloads

# write_agent_review 写出 outer run 根的 Agent 自动审核文件。
def write_agent_review(
    path_smoke_root: Path, run_id: str, source_digest: str,
    remote_server_id: str, dict_phase_payloads: dict[str, dict[str, object]],
    str_archive_hash: str, str_pressure_hash: str,
) -> None:
    """基于远程阶段和归档摘要生成 Agent 审核文件。

    :param path_smoke_root: 当前 retained reports 目录。
    :param run_id: outer run 身份。
    :param source_digest: 上传包内容摘要。
    :param remote_server_id: 目标服务器标识。
    :param dict_phase_payloads: 已通过校验的三阶段摘要。
    :param str_archive_hash: 归档清单摘要。
    :param str_pressure_hash: 压力报告摘要。
    :return: 不返回业务值；审核文件原子写出后结束。
    """

    # shell 可显式绑定 outer run 根，否则由 reports 的父目录推导。
    str_review_path = os.environ.get("VERILOG_GENERATOR_AGENT_REVIEW_PATH", "")  # Agent 审核文件路径文本

    # 审核文件路径必须与本轮 reports 处于同一 retained run bundle。
    path_review = (  # Agent 审核文件的绝对路径
        Path(str_review_path).resolve()  # shell 指定路径的绝对形式
        if str_review_path  # 有显式路径时保持 outer run 绑定
        else path_smoke_root.parent / "agent_review.json"  # 默认 reports 的父目录
    )

    # 审核只基于刚写出的真实阶段、归档和身份摘要，不替代任何远程测试事实。
    dict_review: dict[str, object] = {  # 绑定 run_id、source_digest、阶段退出码/count 与两类摘要哈希
        "schema": 1,  # 审核载荷 schema 版本
        "kind": "agent-review",  # 机器识别的审核类型
        "status": "passed",  # 三阶段及归档事实均已通过
        "reviewed_by": "agent",  # 审核主体固定为当前 Agent

        # 运行身份字段绑定 outer run、源码和服务器。
        "run_id": run_id,  # outer run 的唯一标识字段
        "source_digest": source_digest,  # 上传包源码摘要
        "remote_server_id": remote_server_id,  # 目标服务器标识

        # 阶段字段保留每一层真实退出码和计数。
        "pytest_phases": {  # 三阶段真实 pytest 结果
            str_phase: {  # 当前阶段的审核字段
                "status": dict_phase_payloads[str_phase]["status"],  # 阶段状态
                "exit_code": dict_phase_payloads[str_phase]["exit_code"],  # 阶段退出码
                "count": dict_phase_payloads[str_phase]["count"],  # 阶段用例数
            }
            for str_phase in REMOTE_PHASES  # 固定 targeted、regression、full 顺序
        },

        # 归档和压力摘要用于 Agent 自审的可复算边界。
        "validation_archive_hash": str_archive_hash,  # 工件归档清单摘要
        "skill_pressure_report_hash": str_pressure_hash,  # 压力报告摘要
        "reviewed_at": datetime.now(timezone.utc).isoformat(),  # Agent 审核时间
    }

    # 审核文件父目录由远程命令创建，缺失时按同一 outer run 补齐。
    path_review.parent.mkdir(parents=True, exist_ok=True)

    # 临时文件与最终文件同目录，保证 replace 在同一文件系统内原子完成。
    path_review_temporary = path_review.with_suffix(".json.tmp")  # Agent 审核临时路径

    # 审核正文使用稳定 JSON 格式，便于本地 report-runs 下载后复核。
    path_review_temporary.write_text(
        json.dumps(dict_review, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 原子替换避免 report-runs 读取半写入的审核正文。
    path_review_temporary.replace(path_review)

# _write_remote_environment 固化远端解释器、平台和工具解析事实。
def _write_remote_environment(path_smoke_root: Path, remote_server_id: str) -> str:
    """写出环境事实并返回其 canonical SHA-256。

    :param path_smoke_root: 当前 retained reports 目录。
    :param remote_server_id: 当前远程服务器标识。
    :return: 环境事实载荷的 canonical SHA-256。
    """

    # 环境载荷来自当前远程 Python 进程和工具解析结果。
    dict_environment: dict[str, object] = {  # 远程解释器、平台和工具事实
        "schema": 1,  # 环境证据 schema 版本
        "server_id": remote_server_id,  # 当前远程服务器标识字段
        "cwd": str(Path.cwd()),  # 远程 Python 进程当前目录
        "python_executable": sys.executable,  # 实际使用的 Python 解释器路径
        "python_version": platform.python_version(),  # 当前解释器版本信息
        "platform": platform.platform(),  # 远程平台描述信息
        "uname": " ".join(platform.uname()),  # 内核和机器摘要文本
        "tools": {  # 远程 PATH 中仿真与综合命令解析结果
            str_tool: shutil.which(str_tool) or ""  # 当前工具的绝对路径或缺失标记
            for str_tool in ("xvlog", "xelab", "xsim", "vcs", "verdi", "iverilog", "vvp", "yosys")  # 工具探测顺序
        },
    }

    # 环境文件先落盘，再对不含格式空白的对象计算指纹。
    _write_json(path_smoke_root / "remote_environment.json", dict_environment)

    # 指纹哈希绑定当前解释器、平台、cwd 和工具解析结果。
    return hashlib.sha256(_canonical_bytes(dict_environment)).hexdigest()

# _write_remote_cwd 固化远端 cwd 与本轮 run/source 身份。
def _write_remote_cwd(path_smoke_root: Path, run_id: str, source_digest: str) -> str:
    """写出 cwd 事实并返回不含自引用字段的 canonical SHA-256。

    :param path_smoke_root: 当前 retained reports 目录。
    :param run_id: 外层 retained run 标识。
    :param source_digest: 上传包源码摘要。
    :return: cwd 原始身份载荷的 canonical SHA-256。
    """

    # 目录载荷绑定当前 cwd、run/source 身份；哈希只对原始字段计算。
    dict_cwd: dict[str, object] = {  # 远程当前目录和外层运行身份
        "schema": 1,  # cwd 证据 schema 版本
        "cwd": str(Path.cwd()),  # 远程验证实际工作目录
        "run_id": run_id,  # cwd 证据绑定的 retained run
        "source_digest": source_digest,  # cwd 证据绑定的 staging 包
    }

    # cwd 哈希在添加自身字段前计算，避免自引用。
    str_cwd_hash = hashlib.sha256(_canonical_bytes(dict_cwd)).hexdigest()  # 远程目录身份哈希

    # 便于人工审阅的 cwd 文件同时保留可复算哈希。
    dict_cwd["cwd_hash"] = str_cwd_hash  # 便于本地重算的 cwd canonical 哈希

    # cwd 文件写入后供本地 report-runs 下载。
    _write_json(path_smoke_root / "remote_cwd.json", dict_cwd)

    # 返回不含自引用字段的 cwd 身份哈希。
    return str_cwd_hash

# _load_remote_pressure_inputs 读取 fixture 与 execution 原始载荷。
def _load_remote_pressure_inputs(
    path_smoke_root: Path,
) -> dict[str, object]:
    """返回 fixture 列表、simulator metrics 和执行状态。

    :param path_smoke_root: 当前 retained reports 目录。
    :return: fixture 记录、simulator metrics 与 execution 状态。
    """

    # 压力报告只汇总实际阶段、fixture 和 execution JSON 的已观察结果。
    path_fixture_summary = path_smoke_root / "remote_fixtures" / "summary.json"  # fixture 汇总路径

    # 主流程校验 JSON 提供 simulator backend 和整体执行状态。
    path_validation = path_smoke_root / "remote_execute" / "attempt-001" / "validation.json"  # 主流程校验路径

    # fixture 汇总是运行时生成的原始 JSON。
    dict_fixture_summary = json.loads(path_fixture_summary.read_text(encoding="utf-8"))  # 固定 fixture 原始载荷

    # 主流程验证是运行时生成的原始 JSON。
    dict_validation = json.loads(path_validation.read_text(encoding="utf-8"))  # execution 原始载荷

    # 只把结构正确的 fixture 列表带入压力报告。
    list_fixtures = dict_fixture_summary.get("fixtures", []) if isinstance(dict_fixture_summary, dict) else []  # fixture 记录列表

    # execution metrics 可能不存在，缺失时由压力报告明确反映。
    dict_metrics = dict_validation.get("metrics", {}) if isinstance(dict_validation, dict) else {}  # 执行后端的 simulator metrics 映射

    # 主流程整体状态保持来自 validation.json 的原始布尔事实。
    bool_execute_ok = isinstance(dict_validation, dict) and bool(dict_validation.get("ok"))  # execution 总体状态

    # 具名字典避免调用方把不同证据误配成位置元组。
    return {  # fixture、metrics 和执行状态的具名载荷
        "fixtures": list_fixtures,
        "metrics": dict_metrics,
        "execute_ok": bool_execute_ok,
    }

# _build_pressure_phase_payload 保留三个阶段的最小压力字段。
def _build_pressure_phase_payload(
    dict_phase_payloads: dict[str, dict[str, object]],
) -> dict[str, dict[str, object]]:
    """生成 targeted、regression、full 阶段的压力字段。

    :param dict_phase_payloads: 远程 pytest 阶段原始载荷。
    :return: 供压力报告引用的阶段字段映射。
    """

    # 固定阶段顺序并只保留可复算的状态、计数和命令摘要。
    dict_phase_pressure: dict[str, dict[str, object]] = {  # 三阶段压力字段映射
        str_phase: {  # 当前阶段的最小压力字段
            key: dict_phase_payloads[str_phase][key]  # 从原始阶段提取指定字段
            for key in ("status", "exit_code", "count", "command_hash")  # 阶段压力字段集合
        }
        for str_phase in REMOTE_PHASES  # 按固定 targeted、regression、full 顺序收集
    }

    # 返回不含环境噪声的阶段压力载荷。
    return dict_phase_pressure

# _write_remote_pressure_report 汇总阶段、fixture 和执行后端事实。
def _write_remote_pressure_report(
    path_smoke_root: Path,
    run_id: str,
    source_digest: str,
    dict_phase_payloads: dict[str, dict[str, object]], list_fixtures: list[object],
    dict_metrics: dict[str, object], bool_execute_ok: bool,
) -> str:
    """写出压力报告并返回其载荷和 canonical SHA-256。

    :param path_smoke_root: 当前 retained reports 目录。
    :param run_id: 外层 retained run 标识。
    :param source_digest: 上传包源码摘要。
    :param dict_phase_payloads: targeted、regression、full 阶段载荷。
    :param list_fixtures: 远程 fixture 执行记录。
    :param dict_metrics: 主流程 simulator metrics 映射。
    :param bool_execute_ok: 主流程 validation.json 的最终状态。
    :return: 压力报告载荷的 canonical SHA-256。
    """

    # 先建立带明确语义的空映射，避免多行赋值首行失去右侧注释关联。
    dict_pressure: dict[str, object] = {}  # 远端压力报告字段的结构化容器

    # 三阶段载荷独立生成，避免 aggregate 计数冒充分阶段结果。
    dict_phase_pressure = _build_pressure_phase_payload(dict_phase_payloads)  # 三阶段压力字段

    # fixture 总数和通过数共同构成最小案例回归事实。
    int_fixture_count = len(list_fixtures)  # 远程 fixture 总数

    # 只统计 fixture 载荷中明确标记为 ok 的记录。
    int_fixture_ok_count = sum(1 for item in list_fixtures if isinstance(item, dict) and bool(item.get("ok")))  # 远程 fixture 通过数

    # 读取实际 simulator backend，缺失时保留空字符串而非推测值。
    str_selected_backend = dict_metrics.get("selected_simulator_backend", "")  # validation.json 选择并执行的 simulator backend 名称

    # 先写入本轮身份字段，保证压力摘要绑定 outer run。
    dict_pressure.update(  # 写入压力报告身份字段
        {
            "schema": 1,  # 压力报告 schema 版本
            "run_id": run_id,  # 压力报告绑定的本轮 outer 目录
            "source_digest": source_digest,  # 压力报告绑定的 staging 内容
        }
    )

    # 再写入三阶段的真实退出码、计数和命令摘要。
    dict_pressure.update({"pytest_phases": dict_phase_pressure})  # 写入三阶段压力字段

    # 写入 fixture 总数及显式通过数，避免隐含推断。
    dict_pressure.update(  # 写入 fixture 计数事实
        {
            "fixture_count": int_fixture_count,  # pressure payload 的样例总数
            "fixture_ok_count": int_fixture_ok_count,  # pressure payload 的显式成功数
        }
    )

    # 最后写入主流程状态和实际 simulator backend。
    dict_pressure.update(  # 写入 execution 后端事实
        {
            "execute_ok": bool_execute_ok,  # 主流程整体状态
            "selected_simulator_backend": str_selected_backend,  # 实际 simulator backend
        }
    )

    # 压力报告先写正文，再由 canonical 对象计算摘要哈希。
    _write_json(path_smoke_root / "skill_pressure_report.json", dict_pressure)

    # 计算不含格式空白的压力报告摘要哈希。
    str_pressure_hash = hashlib.sha256(_canonical_bytes(dict_pressure)).hexdigest()  # 压力报告哈希

    # 返回压力报告的 canonical 身份哈希。
    return str_pressure_hash

# _write_validation_archive_manifest 为 retained 工件建立稳定归档清单。
def _write_validation_archive_manifest(path_smoke_root: Path) -> str:
    """写出归档清单并返回 canonical SHA-256。

    :param path_smoke_root: 当前 retained reports 目录。
    :return: 归档清单载荷的 canonical SHA-256。
    """

    # 归档清单覆盖运行日志、阶段摘要、fixture 和 execution 工件。
    list_archive_files: list[dict[str, object]] = []  # 归档清单中的实际文件记录

    # 排序保证同一 retained 目录在不同文件系统上得到相同顺序。
    for path_file in sorted(path_smoke_root.rglob("*")):

        # 目录本身没有字节内容，不进入归档清单。
        if not path_file.is_file():

            # 目录没有字节内容，跳过当前项。
            continue

        # 归档路径统一相对于 smoke 根目录表达。
        str_relative_path = path_file.relative_to(path_smoke_root).as_posix()  # 归档内的 POSIX 路径

        # 身份文件和 Python 缓存由总表字段单独绑定，不参与清单哈希。
        if str_relative_path in IDENTITY_FILE_NAMES or "__pycache__" in path_file.parts:

            # 身份文件由总表字段绑定，避免哈希循环。
            continue

        # Python 字节码不是验证工件，继续保留可审阅的源和报告文件。
        if path_file.suffix in {".pyc", ".pyo"}:

            # 字节码文件不进入 retained 归档。
            continue

        # 读取原始字节，大小和哈希都绑定未改写的工件。
        bytes_content = path_file.read_bytes()  # 当前归档工件的原始字节

        # 追加一条包含路径、大小和摘要的归档工件事实。
        list_archive_files.append(  # 追加一条归档工件事实
            {
                "path": str_relative_path,
                "size": len(bytes_content),
                "sha256": hashlib.sha256(bytes_content).hexdigest(),
            }
        )

    # 归档清单本身也采用稳定 schema，但排除自身文件。
    dict_archive: dict[str, object] = {"schema": 1, "files": list_archive_files}  # retained 归档清单

    # 归档清单正文写入后供本地 report-runs 下载。
    _write_json(path_smoke_root / "validation_archive_manifest.json", dict_archive)

    # 返回所有非身份工件的 canonical 清单哈希。
    return hashlib.sha256(_canonical_bytes(dict_archive)).hexdigest()

# _write_remote_evidence_payload 写出总表并返回其结构化载荷。
def _write_remote_evidence_payload(
    path_smoke_root: Path,
    dict_evidence_identity: dict[str, str],
    dict_phase_payloads: dict[str, dict[str, object]],
) -> dict[str, object]:
    """写出远程测试总表并返回总表对象。

    :param path_smoke_root: 当前 retained reports 目录。
    :param dict_evidence_identity: run/source/server 及各类摘要哈希。
    :param dict_phase_payloads: targeted、regression、full 阶段载荷。
    :return: 已写出的远程测试总表对象。
    """

    # 最终总表只引用真实文件载荷的哈希，不把自身写入自身摘要。
    dict_evidence: dict[str, object] = {  # 远程测试总表
        "schema": 1,  # 总表 schema 版本
        "kind": "remote-test-evidence",  # 本对象的机器识别类型
        "run_id": dict_evidence_identity["run_id"],  # 总表绑定的外层目录身份
        "source_digest": dict_evidence_identity["source_digest"],  # 总表绑定的上传源码摘要
        "remote_server_id": dict_evidence_identity["remote_server_id"],  # 总表声明的目标服务器
        "remote_fingerprint_hash": dict_evidence_identity["remote_fingerprint_hash"],  # 总表引用的环境指纹
        "remote_cwd_hash": dict_evidence_identity["remote_cwd_hash"],  # 总表引用的工作目录指纹
        "validation_archive_hash": dict_evidence_identity["validation_archive_hash"],  # 总表引用的工件清单指纹
        "skill_pressure_report_hash": dict_evidence_identity["skill_pressure_report_hash"],  # 总表引用的覆盖压力指纹
        "remote_pytest": dict_phase_payloads,  # 三阶段 pytest 原始摘要
        "environment_path": "remote_environment.json",  # 环境事实相对路径
        "cwd_path": "remote_cwd.json",  # cwd 事实相对路径
        "pressure_report_path": "skill_pressure_report.json",  # 压力报告相对路径
        "archive_manifest_path": "validation_archive_manifest.json",  # 归档清单相对路径
    }

    # 总表写入后由 completion 生成器在外层追加 self hash。
    _write_json(path_smoke_root / "remote_test_evidence.json", dict_evidence)

    # 返回同一对象，便于远程 wrapper 立即复核字段。
    return dict_evidence

# write_remote_test_evidence 生成一轮远程验证的完整身份载荷。
def write_remote_test_evidence(
    path_smoke_root: Path,
    run_id: str,
    source_digest: str,
    remote_server_id: str,
) -> dict[str, object]:
    """生成并写出一轮远程验证的完整身份载荷。

    :param path_smoke_root: 当前 retained smoke 运行目录。
    :param run_id: 外层远程运行标识。
    :param source_digest: 上传验证包的源码摘要。
    :param remote_server_id: 本次远程验证使用的服务器标识。
    :return: 已写出的远程测试总表对象。
    :raises FileNotFoundError: 运行目录或既定执行工件不存在时抛出。
    :raises ValueError: 阶段或执行工件结构不满足证据合同时抛出。
    """

    # retained smoke 根必须存在，避免把证据写入任意当前目录。
    path_smoke_root = path_smoke_root.resolve()  # 当前远程 retained 目录的绝对路径

    # 远程编排只允许在已创建的 smoke 目录内追加身份文件。
    if not path_smoke_root.is_dir():

        # 缺失运行根说明远程执行尚未建立证据边界。
        raise FileNotFoundError("> ERR: [Python] retained smoke root is unavailable")

    # 只接受真实阶段摘要，不从 aggregate 计数推导阶段结果。
    dict_phase_payloads = _read_phase_payloads(path_smoke_root)  # 三阶段 pytest 原始载荷

    # 环境指纹绑定当前解释器、平台和工具解析结果。
    str_fingerprint_hash = _write_remote_environment(path_smoke_root, remote_server_id)  # 环境指纹哈希

    # cwd 指纹绑定远程工作目录和本轮 run/source 身份。
    str_cwd_hash = _write_remote_cwd(path_smoke_root, run_id, source_digest)  # 远程 cwd 身份哈希

    # 压力输入来自已写出的 fixture 与 execution 原始载荷。
    dict_pressure_inputs = _load_remote_pressure_inputs(path_smoke_root)  # fixture、metrics 和执行状态

    # fixture 列表独立绑定到压力报告的计数输入。
    list_fixtures: list[object] = dict_pressure_inputs["fixtures"]  # 远程 fixture 执行记录

    # simulator metrics 独立绑定到后端选择摘要。
    dict_metrics: dict[str, object] = dict_pressure_inputs["metrics"]  # validation.json 中提供的 simulator metrics 映射

    # execution 状态独立绑定到主流程结果摘要。
    bool_execute_ok: bool = dict_pressure_inputs["execute_ok"]  # 主流程 validation.json 状态

    # 压力报告保存三阶段计数、fixture 状态和 simulator backend。
    str_pressure_hash = _write_remote_pressure_report(  # 生成压力报告并返回 canonical SHA-256 指纹
        path_smoke_root, run_id, source_digest,  # retained 根、run 标识和源码摘要共同构成执行边界
        dict_phase_payloads, list_fixtures,  # 三阶段原始载荷与 fixture 记录用于重建压力计数
        dict_metrics, bool_execute_ok,  # simulator metrics 与 validation 状态确认实际后端和退出结果
    )

    # retained 清单绑定所有非身份工件的路径、大小和内容摘要。
    str_archive_hash = _write_validation_archive_manifest(path_smoke_root)  # retained 归档清单哈希

    # 总表身份映射把全部摘要哈希绑定到本轮 retained run。
    dict_evidence_identity = dict(  # 远程总表的身份与摘要哈希映射
        run_id=run_id,  # outer run 唯一标识
        source_digest=source_digest,  # 上传包的 source_digest 绑定

        # 服务器与环境指纹共同限制证据复用范围。
        remote_server_id=remote_server_id,  # SSH 路由使用的 server 身份
        remote_fingerprint_hash=str_fingerprint_hash,  # Python 环境指纹摘要

        # cwd 与归档指纹共同约束 retained 工件边界。
        remote_cwd_hash=str_cwd_hash,  # cwd 身份摘要
        validation_archive_hash=str_archive_hash,  # retained 工件清单摘要

        # 阶段压力指纹记录 targeted、regression、full 的覆盖事实。
        skill_pressure_report_hash=str_pressure_hash,  # 三阶段压力摘要
    )

    # 写入绑定身份哈希和阶段原始载荷的远程测试总表。
    dict_evidence = _write_remote_evidence_payload(  # 生成并写出本轮远程测试总表文件
        path_smoke_root,  # 本轮 retained 报告文件的父目录
        dict_evidence_identity,  # run、源码、服务器和各类摘要哈希
        dict_phase_payloads,  # targeted、regression、full 的真实阶段摘要
    )

    # 由独立函数在 outer run 根生成 Agent 审核文件，避免总表生成器承担过多职责。
    write_agent_review(
        path_smoke_root,  # 本轮 retained reports 目录
        run_id,  # Agent 审核绑定的运行槽位

        # 继续传入源码、服务器和阶段摘要身份。
        source_digest,  # Agent 审核使用的上传包内容摘要
        remote_server_id,  # Agent 审核声明的远程归属标识
        dict_phase_payloads,  # Agent 审核读取的三个阶段事实

        # 最后绑定归档与压力报告摘要。
        str_archive_hash,  # Agent 审核绑定的 retained 清单摘要
        str_pressure_hash,  # Agent 审核绑定的覆盖压力摘要
    )

    # 提供给远程包装器立即复核总表字段。
    return dict_evidence

# main 从远程编排环境变量读取本轮身份并写出证据。
def main() -> None:
    """生成当前远程 smoke 运行的环境和阶段证据。

    :param: 本函数不接收命令行业务参数，输入来自远程编排环境变量。
    :return: 不返回业务值；证据文件写入完成后结束。
    :raises RuntimeError: 远程编排未提供必要身份变量时抛出。
    """

    # smoke 根必须由同一份远程 shell 显式导出。
    str_smoke_root = os.environ.get("VERILOG_GENERATOR_SMOKE_RUN_DIR", "")  # retained smoke 根目录文本

    # 外层 run、源码摘要和 server id 共同构成远程证据身份。
    str_run_id = os.environ.get("VERILOG_GENERATOR_RUN_ID", "")  # 绑定远程总表的 outer retained run 标识

    # 源码摘要把远程证据绑定到当前上传包，而不是任意旧 staging。
    str_source_digest = os.environ.get("VERILOG_GENERATOR_SOURCE_DIGEST", "")  # 当前 staging 包的源码摘要

    # server id 进入审核与环境归属字段，避免跨服务器复用同一份证据。
    str_remote_server_id = os.environ.get("VERILOG_GENERATOR_REMOTE_SERVER_ID", "")  # 审核载荷使用的服务器标识

    # 缺失任何身份字段都禁止把默认值写成看似真实的证据。
    if not str_smoke_root or not str_run_id or not str_source_digest or not str_remote_server_id:

        # 结构化错误提示上层重新建立远程运行合同。
        raise RuntimeError("> ERR: [Python] remote evidence identity variables are required")

    # 生成本轮环境、目录、压力、归档和 pytest 总表。
    write_remote_test_evidence(  # 写出远程测试证据总表
        Path(str_smoke_root),
        str_run_id,
        str_source_digest,
        str_remote_server_id,
    )

# 仅在远程 wrapper 直接执行模块时生成证据。
if __name__ == "__main__":

    # 入口不接收参数，所有输入均来自同一 retained run 环境。
    main()
