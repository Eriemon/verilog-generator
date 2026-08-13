"""提供 CLI 命令处理器共享的文件读取、状态记录和 readiness 判定。"""

# future annotations 避免运行期解析复杂类型提示。
from __future__ import annotations

# 标准库只负责 JSON 解码、路径处理和类型标注。
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 提取器复用 manifest 解析规则，避免 CLI 层复制模型响应格式判断。
from .extractor import parse_manifest
from scripts.python.validation.validation import readiness_at_least
from .workspace import require_workspace_path, update_workflow_state

@dataclass(frozen=True)
class PromptReportContext:
    """承载 prompt 报告负载需要的全部输入。"""

    # output 是已经渲染完成、准备写入文件的 prompt 文本。
    output: str  # prompt 输出文本

    # stage 标记 prompt 渲染所处的工作流阶段。
    stage: str  # prompt 工作流阶段

    # budget 记录 prompt 预算档位，便于定位上下文裁剪策略。
    budget: str  # prompt 预算档位

    # subfunction 限定 staged prompt 聚焦的子功能。
    subfunction: str | None  # prompt 聚焦子功能

    # context_manifest 保存 manifest 中的上游文件列表。
    context_manifest: dict | None  # manifest 上游文件描述

    # context_dir 指向额外加入 prompt 的上下文目录。
    context_dir: Path | None  # prompt 上下文目录

    # vector_contract 标记参考向量语义合同是否进入 prompt。
    vector_contract: dict | None  # 向量语义合同

    # decision 标记人工决策约束是否进入 prompt。
    decision: dict | None  # 人工决策约束

# record_state 是所有子命令写 workflow-state 的统一边界。
def record_state(args: Any, event: str, payload: dict) -> None:
    """按 CLI 参数中的 state/no-state 设置记录 workflow 状态。

    参数:
        args: CLI 子命令参数对象。
        event: workflow-state 事件名称。
        payload: 写入 workflow-state 的事件负载。

    返回:
        无返回值。
    """

    # state 可能不存在于不支持状态副作用的子命令。
    path_state = getattr(args, "state", None)  # workflow-state 输出路径

    # no_state 只在显式禁用状态写入时关闭副作用。
    bool_state_enabled = not getattr(args, "no_state", False)  # 状态写入开关

    # 状态更新集中走 workspace helper，保持默认路径策略一致。
    update_workflow_state(path_state, event, payload, enabled=bool_state_enabled)

# read_json 只允许读取 workspace 约束内的 JSON 对象。
def read_json(path: Path | None) -> dict:
    """读取 workspace 内 JSON 对象；空路径返回空对象。

    参数:
        path: 可选 JSON 文件路径。

    返回:
        已验证为对象的 JSON 内容。

    异常:
        ValueError: JSON 无法解析，或根节点不是对象。
    """

    # 缺省可选 JSON 输入时，调用方按空配置处理。
    if path is None:

        # 空对象表示调用方没有提供可选 JSON 文件。
        return {}

    # JSON 输入必须通过 workspace 边界检查。
    path_json = require_workspace_path(path, purpose="JSON path", must_exist=True)  # 受控 JSON 路径

    # JSON 解码错误需要带上路径，便于命令行用户定位输入文件。
    try:

        # 读取 UTF-8 JSON 文本并解析为 Python 值。
        obj_json_root: object = json.loads(path_json.read_text(encoding="utf-8"))  # JSON 根对象候选值

    # 解码失败时保留原始 JSON 异常上下文。
    except json.JSONDecodeError as exc:

        # 错误消息保留原始 JSON 异常上下文。
        raise ValueError(f"> ERR: [Python] Invalid JSON in {path_json}: {exc}") from exc

    # CLI 合同只接受 JSON object，避免数组或标量被误当配置。
    if not isinstance(obj_json_root, dict):

        # 非对象输入无法安全传递给后续 workflow。
        raise ValueError(f"> ERR: [Python] Expected JSON object in {path_json}.")

    # 返回已验证的 JSON 对象。
    return obj_json_root

# read_json_anywhere 供 route-workflow 读取显式请求文件，不套 workspace 边界。
def read_json_anywhere(path: Path) -> dict:
    """读取显式给定路径上的 JSON 对象。

    参数:
        path: 调用方显式提供的 JSON 文件路径。

    返回:
        已验证为对象的 JSON 内容。

    异常:
        ValueError: JSON 无法解析，或根节点不是对象。
    """

    # route-workflow 需要允许读取调用方显式传入的 request JSON。
    try:

        # Path(path) 兼容 argparse 传入的 Path 或等价路径值。
        obj_json_root: object = json.loads(Path(path).read_text(encoding="utf-8"))  # 路由请求 JSON 候选值

    # 解码失败时保留原始请求路径。
    except json.JSONDecodeError as exc:

        # 解码失败时保留原始路径，方便用户修正请求文件。
        raise ValueError(f"> ERR: [Python] Invalid JSON in {path}: {exc}") from exc

    # 路由请求必须是对象，才能读取 summary/spec/rtl 等字段。
    if not isinstance(obj_json_root, dict):

        # 非对象 JSON 没有可路由字段。
        raise ValueError(f"> ERR: [Python] Expected JSON object in {path}.")

    # 返回可供 route_verilog_entry 消费的请求对象。
    return obj_json_root

# read_manifest 兼容 JSON manifest 和 fenced response manifest 两种输入。
def read_manifest(path: Path) -> dict:
    """读取 context manifest，自动兼容 JSON 对象或 fenced manifest 文本。

    参数:
        path: context manifest 文件路径。

    返回:
        解析后的 manifest 对象。
    """

    # manifest 路径必须在 workspace 内存在。
    path_manifest = require_workspace_path(path, purpose="context manifest", must_exist=True)  # 受控 manifest 输入文件

    # manifest 文本可能是 JSON，也可能是 fenced response。
    str_manifest_text = path_manifest.read_text(encoding="utf-8")  # manifest 原始文本

    # 去除首尾空白后判断是否是 JSON object。
    str_stripped_text = str_manifest_text.strip()  # manifest 判别文本

    # 以左花括号开头时按 JSON manifest 读取。
    if str_stripped_text.startswith("{"):

        # JSON manifest 直接交给 json 解析器。
        return json.loads(str_stripped_text)

    # 非 JSON 输入按模型响应 manifest 规则提取。
    return parse_manifest(str_manifest_text)

# resolve_codegen_plan 从 spec 相对路径中恢复上游 codegen plan。
def resolve_codegen_plan(spec: dict, spec_path: Path) -> dict:
    """读取 spec 声明的 codegen_plan_path；缺失或不存在时返回空对象。

    参数:
        spec: 已解析的规格对象。
        spec_path: 规格文件路径，用于解析相对计划路径。

    返回:
        codegen plan 对象；缺失时为空对象。
    """

    # codegen_plan_path 是可选的计划种子输入。
    plan_path = spec.get("codegen_plan_path")  # spec 内声明的计划相对路径

    # 没有声明计划时，prompt 渲染按无计划模式运行。
    if not plan_path:

        # 空对象表示没有可注入的 codegen plan。
        return {}

    # 计划路径相对 spec 文件所在目录解析。
    path_candidate = (spec_path.parent / plan_path).resolve()  # codegen plan 候选路径

    # 候选路径存在时复用受控 JSON 读取。
    if path_candidate.exists():

        # 复用 JSON 对象校验，避免 prompt 注入非对象配置。
        return read_json(path_candidate)

    # 声明但缺失时不失败，保持旧 prompt 行为兼容。
    return {}

# build_prompt_report_payload 汇总 prompt 输出规模和注入上下文状态。
def build_prompt_report_payload(context: PromptReportContext) -> dict[str, object]:
    """生成 prompt 报告 JSON 负载。

    参数:
        context: prompt 报告负载的完整输入上下文。

    返回:
        可序列化的 prompt 报告摘要。
    """

    # manifest_files 只在 manifest 是对象时读取 files 字段。
    if isinstance(context.context_manifest, dict):

        # manifest 对象中的 files 字段描述 prompt 已注入的文件。
        list_manifest_files = context.context_manifest.get("files", [])  # manifest 注入文件清单

    # manifest 缺失或非对象时按空文件清单处理。
    else:

        # 非对象 manifest 不提供文件列表，按空清单统计。
        list_manifest_files = []  # manifest 缺省注入文件清单

    # manifest_artifacts 统计 manifest 中列出的上游产物数量。
    int_manifest_artifacts = len(list_manifest_files)  # 注入 prompt 的 manifest 文件数量

    # context_dir 额外代表一个目录级上下文来源。
    int_context_artifacts = int_manifest_artifacts + (1 if context.context_dir else 0)  # prompt 上下文来源数量

    # 返回结构保持 version=1，供 smoke 和用户脚本稳定读取。
    return {
        "version": 1,

        # 输出规模字段用于快速判断 prompt 预算是否失控。
        "chars": len(context.output),
        "approx_tokens": max(1, len(context.output) // 4),

        # 上下文字段描述本次 prompt 注入了多少外部证据。
        "context_artifacts": int_context_artifacts,
        "has_vector_contract": bool(context.vector_contract),
        "has_decision": bool(context.decision),

        # 模式字段帮助 eval 和调试区分 prompt 渲染路径。
        "budget": context.budget,
        "subfunction": context.subfunction,
        "stage": context.stage,
    }

# synth_readiness_payload 从 validation report 中提取综合 readiness 摘要。
def synth_readiness_payload(report: dict[str, Any], *, readiness: str) -> dict[str, Any]:
    """生成 synth_readiness.json 的稳定摘要。

    参数:
        report: validation report 对象。
        readiness: 用户请求的 readiness 等级。

    返回:
        可序列化的综合 readiness 摘要。
    """

    # metrics 缺失时按空对象处理，保持旧 report 兼容。
    dict_metrics = report.get("metrics", {}) if isinstance(report, dict) else {}  # 提取工具执行摘要来源

    # 输出字段保持旧版文件合同，供回归脚本读取。
    return {
        "version": 1,
        "requested_readiness": readiness,
        "selected_simulator_backend": dict_metrics.get("selected_simulator_backend"),
        "executed_tools": dict_metrics.get("executed_tools", []),
        "missing_preferred_backends": dict_metrics.get("missing_preferred_backends", []),
        "selection_policy": dict_metrics.get("selection_policy"),
        "validation_ok": bool(report.get("ok")),
    }

# cli_run_external 集中执行 remote-first 外部验证策略。
def cli_run_external(no_external: bool, external_target: str, readiness: str) -> bool:
    """根据 CLI 开关和 readiness 判断是否允许本地外部工具执行。

    参数:
        no_external: 是否显式禁用外部工具。
        external_target: 外部验证目标位置。
        readiness: 当前请求的 readiness 等级。

    返回:
        允许本地外部验证时返回 True，否则返回 False。

    异常:
        ValueError: readiness 需要外部工具但目标不是显式 local。
    """

    # 显式禁用外部工具时，所有 readiness 都只做本地静态路径。
    if no_external:

        # 调用方应跳过 xsim/Vivado 等外部执行。
        return False

    # static readiness 不需要 compile 级工具。
    if not readiness_at_least(readiness, "compile"):

        # 未达到 compile 门槛时不启动外部工具。
        return False

    # 外部验证默认 remote-first，local 必须由用户显式选择。
    if external_target != "local":

        # 错误消息保留 canonical 远程验证脚本路径，便于用户直接复制执行。
        str_remote_validation_script = "scripts/python/remote/remote_validate_verilog_skill.py"  # 远程验证脚本规范路径

        # 阻止 CLI 暗中在本机运行外部 FPGA 工具链。
        raise ValueError(
            f"> ERR: [Python] External validation is remote-first. Use {str_remote_validation_script}, "
            "or pass --external-target local only after the user explicitly approves local external validation."
        )

    # local 已显式选择且 readiness 达到 compile，允许调用方运行外部验证。
    return True
