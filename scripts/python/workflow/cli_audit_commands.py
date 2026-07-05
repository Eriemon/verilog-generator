"""实现 audit、ingest 和 decompose 相关 CLI 子命令。"""

# future annotations 避免 argparse 类型提示在运行期求值。
from __future__ import annotations

# 标准库只负责 argparse 命名空间和当前工作目录解析。
import argparse
from pathlib import Path

# 审计入口分别覆盖向量、接口和参考模型合同。
from .interface_contract import audit_interface
from scripts.python.existing_rtl.semantic_contract import audit_semantic_model
from .vectors import audit_vectors

# 规格证据和拆解流程保持在对应核心模块内。
from scripts.python.validation.evidence import ingest_sources
from .planning import decompose_spec
from .spec import read_spec

# CLI 支撑层统一读取 JSON 与记录状态副作用。
from .cli_support import read_json, record_state
from .trace import append_trace_event
from .workspace import require_workspace_path, require_write_path, write_json

# cmd_audit_vectors 评估测试向量包与规格合同的一致性。
def cmd_audit_vectors(args: argparse.Namespace) -> int:
    """处理 audit-vectors 子命令。

    参数:
        args: argparse 解析出的命名空间，需包含 vectors、out 和工作区状态选项。
    返回:
        CLI 退出码；向量审计报告成功写出时返回 0。
    """

    # vectors 输入必须是 workspace 内已存在的文件或目录。
    path_vectors = require_workspace_path(args.vectors, purpose="vectors path", must_exist=True)  # 测试向量输入路径

    # audit 报告输出位置必须通过写入边界检查。
    path_output = require_write_path(args.out, purpose="vector audit output")  # 向量审计报告路径

    # audit_vectors 返回稳定 JSON 对象，供门禁和报告读取。
    dict_vector_contract = audit_vectors(path_vectors)  # 向量审计合同

    # 写入机器可读向量审计报告。
    write_json(path_output, dict_vector_contract)

    # 记录审计输入与输出，便于 workflow-state 串联。
    record_state(args, "audit_vectors", {"vectors": path_vectors, "output": path_output})

    # 审计命令成功写出报告即返回 0。
    return 0

# cmd_audit_interface 检查生成产物的接口合同。
def cmd_audit_interface(args: argparse.Namespace) -> int:
    """处理 audit-interface 子命令。

    参数:
        args: argparse 解析出的命名空间，需包含 target、path、out 和可选 trace。
    返回:
        CLI 退出码；接口审计报告和可选 trace 写入成功时返回 0。
    """

    # artifact path 指向待审计的 RTL 或产物目录。
    path_artifact = require_workspace_path(args.path, purpose="artifact path", must_exist=True)  # 接口审计目标路径

    # interface audit 输出必须落在允许写入的位置。
    path_output = require_write_path(args.out, purpose="interface audit output")  # 接口审计报告路径

    # audit_interface 负责提取目标接口摘要和哈希。
    dict_interface_contract = audit_interface(args.target, path_artifact)  # 接口审计合同

    # 写入接口审计 JSON 报告。
    write_json(path_output, dict_interface_contract)

    # workflow-state 记录接口审计事实。
    record_state(args, "audit_interface", {"target": args.target, "path": path_artifact, "output": path_output})

    # trace 仅在用户显式传入 trace 时追加审计摘要。
    if args.trace:

        # interface_sha256 让后续诊断能定位接口漂移。
        dict_trace_event = {  # 接口审计 trace 事件
            "event": "audit_interface",  # trace 事件类型
            "target": args.target,  # 审计目标类型
            "path": path_artifact,  # 审计目标路径
            "output": path_output,  # 审计报告路径
            "interface_sha256": dict_interface_contract.get("interface_sha256"),  # 接口摘要哈希
        }

        # 追加接口审计事件。
        append_trace_event(args.trace, dict_trace_event)

    # 接口审计成功即返回 0。
    return 0

# cmd_audit_semantic_model 检查 Python semantic model 的向量合同。
def cmd_audit_semantic_model(args: argparse.Namespace) -> int:
    """处理 audit-semantic 子命令。

    参数:
        args: argparse 解析出的命名空间，需包含 path、out 和可选 trace。
    返回:
        CLI 退出码；参考模型审计报告和可选 trace 写入成功时返回 0。
    """

    # semantic model 输入必须存在。
    path_artifact = require_workspace_path(args.path, purpose="semantic model path", must_exist=True)  # 参考模型路径

    # reference audit 报告路径必须允许写入。
    path_output = require_write_path(args.out, purpose="reference audit output")  # 参考模型审计报告路径

    # audit_semantic_model 统计可用测试 case 和合同摘要。
    dict_semantic_contract = audit_semantic_model(path_artifact)  # 参考模型审计合同

    # 写入 reference audit JSON。
    write_json(path_output, dict_semantic_contract)

    # workflow-state 记录 reference audit 产物。
    record_state(args, "audit_semantic_model", {"path": path_artifact, "output": path_output})

    # trace 仅在用户提供路径时记录 case 覆盖数量。
    if args.trace:

        # trace 事件记录参考覆盖数量，帮助后续判断模型合同是否空跑。
        dict_trace_event = {  # 参考模型审计 trace 事件
            "event": "audit_semantic_model",  # 标识参考模型审计事件
            "path": path_artifact,  # 被审计的参考模型文件
            "output": path_output,  # 参考模型审计 JSON 输出
            "case_count": dict_semantic_contract.get("case_count"),  # 参考 case 数量
        }

        # 追加参考模型审计事件。
        append_trace_event(args.trace, dict_trace_event)

    # 参考模型审计成功即返回 0。
    return 0

# cmd_ingest_spec 将外部规格证据整理为结构化 JSON。
def cmd_ingest_spec(args: argparse.Namespace) -> int:
    """处理 ingest-spec 子命令。

    参数:
        args: argparse 解析出的命名空间，需包含 source、sidecar、out 和状态记录选项。
    返回:
        CLI 退出码；规格证据 JSON 写出并记录状态后返回 0。
    """

    # 当前工作目录作为 evidence 读取的根边界。
    path_root = Path.cwd()  # 证据归一化根目录

    # sidecar 缺省为空列表，避免把 None 传入 ingest 流程。
    list_sidecars = args.sidecar or []  # 附加证据路径列表

    # ingest_sources 汇总 markdown、JSON 或旁证文件内容。
    dict_evidence = ingest_sources(args.source, path_root, sidecars=list_sidecars)  # 规格证据对象

    # evidence 输出路径必须通过写入边界。
    path_output = require_write_path(args.out, purpose="evidence output")  # 规格证据输出路径

    # 写入结构化 evidence JSON。
    write_json(path_output, dict_evidence)

    # workflow-state 记录 evidence 来源。
    record_state(args, "ingest_spec", {"sources": args.source, "output": path_output})

    # ingest 成功即返回 0。
    return 0

# cmd_decompose 将 RTL 规格拆成代码生成计划。
def cmd_decompose(args: argparse.Namespace) -> int:
    """处理 decompose 子命令。

    参数:
        args: argparse 解析出的命名空间，需包含 spec、evidence、out 和状态记录选项。
    返回:
        CLI 退出码；代码生成计划 JSON 写出并记录状态后返回 0。
    """

    # spec 输入必须是 workspace 内已存在的 RTL 规格。
    path_spec = require_workspace_path(args.spec, purpose="spec path", must_exist=True)  # RTL 规格输入路径

    # evidence 是可选的规格理解证据。
    dict_evidence = read_json(args.evidence) if args.evidence else None  # 规格拆解证据

    # read_spec 校验 target=rtl 后再交给 planning 模块。
    dict_spec = read_spec(path_spec, target="rtl")  # RTL 规格对象

    # decompose_spec 把 RTL 规格拆成可逐阶段执行的模块生成计划。
    dict_plan = decompose_spec(dict_spec, target="rtl", evidence=dict_evidence)  # 按模块阶段拆分的 RTL 代码生成计划

    # 拆解结果写入位置必须通过工作区写边界检查。
    path_output = require_write_path(args.out, purpose="plan output")  # 代码生成计划路径

    # 将模块生成阶段和依赖关系保存为机器可读计划。
    write_json(path_output, dict_plan)

    # workflow-state 串联规格来源与本次计划产物。
    record_state(args, "decompose", {"spec": path_spec, "output": path_output})

    # 计划文件已经落盘后交还成功退出码给 CLI。
    return 0
