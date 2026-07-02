"""提供本地可复现的 workflow 评估场景集合。"""

# 延迟注解解析，避免导入期求值复杂类型提示。
from __future__ import annotations

# 标准库依赖只负责输出路径和通用 JSON 形状标注。
from pathlib import Path
from typing import Any

# 运行时模块提供事件指标评估和 JSON 写出能力。
from .evaluation import evaluate_events
from .workspace import write_json

# 公开入口生成所有内置评估场景并写出 JSON 报告。
def run_eval_suite(out_path: Path) -> dict[str, Any]:
    """运行内置 workflow 评估场景并保存摘要。

    参数:
        out_path: 评估报告写出的目标 JSON 路径。

    返回:
        包含场景明细和状态统计的评估报告字典。
    """

    # 场景顺序体现从自动修复到人工阻塞的典型 workflow 覆盖面。
    list_scenarios = [
        _dependency_recovery(),  # 依赖错误经下一轮修复的场景
        _toolchain_blocker(),  # 外部工具缺失导致阻塞的场景
        _interface_drift(),  # Python/Verilog 接口漂移被拦截的场景
        _human_decision_feedback(),  # 人工决策恢复 workflow 的场景
        _semantic_output_drift(),  # reference vector 输出漂移的场景
    ]

    # 报告主体保留固定版本和描述，供评估脚本稳定消费。
    dict_payload = {
        "version": 1,  # 评估报告结构版本
        "description": (  # 报告顶部的人类可读覆盖范围
            "Verilog-only scenarios cover dependency recovery, interface drift, "  # 自动修复与接口漂移范围
            "tool blockers, and human feedback."  # 工具阻塞与人工反馈范围
        ),  # 完整英文描述保持对外文本兼容
        "scenarios": list_scenarios,  # 场景明细列表
    }

    # 三类状态都算作评估闭环可接受的终态。
    set_passed_statuses = {"passed", "blocked_toolchain", "blocked_human"}  # 可接受终态集合

    # 摘要字段保持既有 JSON 形状，便于外部报告继续读取。
    dict_payload["summary"] = {
        "scenario_count": len(list_scenarios),  # 评估场景总数
        "passed": sum(  # 可接受终态场景数
            1  # 单个命中场景的计数增量
            for scenario_item in list_scenarios  # 当前参与终态统计的场景
            if scenario_item["metrics"].get("final_status") in set_passed_statuses  # 可接受终态过滤条件
        ),  # 进入可接受终态的场景数
        "status_counts": {  # 终态分布统计
            status_name: sum(  # 当前终态出现次数
                1  # 单个匹配终态的计数增量
                for scenario_item in list_scenarios  # 参与当前终态比对的场景
                if scenario_item["metrics"].get("final_status") == status_name  # 当前终态匹配条件
            )
            for status_name in sorted(set_passed_statuses | {"failed", "invalid_response"})  # 固定顺序的终态名称
        },  # 各终态出现次数
    }

    # 将评估报告写入调用方指定位置，供 CI 或手工审阅。
    write_json(out_path, dict_payload)

    # 返回同一份报告对象，方便库调用方不读文件即可断言。
    return dict_payload

# 场景构造器统一补齐 metrics 字段。
def _scenario(name: str, description: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """用统一形状包装评估场景并即时计算事件指标。

    参数:
        name: 场景稳定标识。
        description: 场景的人类可读说明。
        events: 用于评估 workflow 状态的事件序列。

    返回:
        包含场景元数据、事件和 metrics 摘要的字典。
    """

    # metrics 由事件序列即时计算，避免测试 fixture 手工维护摘要。
    return {"name": name, "description": description, "events": events, "metrics": evaluate_events(events)}

# 依赖恢复场景验证第二轮生成能带着上一轮诊断继续前进。
def _dependency_recovery() -> dict[str, Any]:
    """构造依赖错误经 repair budget 修复后通过的评估场景。

    参数:
        无。

    返回:
        依赖恢复场景的评估字典。
    """

    # 该场景从静态验证失败开始，最终在 repair budget 下通过。
    return _scenario(
        "dependency_recovery",
        "A failed first Verilog attempt is repaired by carrying prompt memory into the next attempt.",
        [
            {"event": "prompt", "attempt_id": "dep-a1", "stage": "rtl", "subfunction": "add_round_key"},
            {
                "event": "validate",
                "attempt_id": "dep-a1",
                "readiness": "static",
                "ok": False,
                "error_sources": ["dependency_issue"],
            },
            {"event": "reflect", "attempt_id": "dep-a1", "error_sources": ["dependency_issue"], "action": "regenerate"},
            {
                "event": "prompt",
                "attempt_id": "dep-a2",
                "stage": "rtl",
                "subfunction": "add_round_key",
                "budget": "repair",
            },
            {"event": "validate", "attempt_id": "dep-a2", "readiness": "static", "ok": True, "error_sources": []},
            {"event": "workflow_attempt", "attempt_id": "dep-a2", "status": "passed"},
        ],
    )

# 工具链阻塞场景验证 implement readiness 不会被静态通过掩盖。
def _toolchain_blocker() -> dict[str, Any]:
    """构造外部 Verilog 工具不可用时合理阻塞的评估场景。

    参数:
        无。

    返回:
        工具链阻塞场景的评估字典。
    """

    # 该场景以 blocked_toolchain 作为合理终态。
    return _scenario(
        "toolchain_blocker",
        "Implementation readiness stops when an external Verilog tool is unavailable.",
        [
            {"event": "prompt", "attempt_id": "tool-a1", "stage": "rtl", "subfunction": "top"},
            {
                "event": "validate",
                "attempt_id": "tool-a1",
                "readiness": "implement",
                "ok": False,
                "error_sources": ["toolchain_issue"],
            },
            {"event": "reflect", "attempt_id": "tool-a1", "error_sources": ["toolchain_issue"], "action": "ask_human"},
            {"event": "workflow_attempt", "attempt_id": "tool-a1", "status": "blocked_toolchain"},
        ],
    )

# 接口漂移场景验证 verify_stage 能在最终接纳前拦截不一致。
def _interface_drift() -> dict[str, Any]:
    """构造 Python 与 Verilog 接口漂移先被拦截再修复的场景。

    参数:
        无。

    返回:
        接口漂移场景的评估字典。
    """

    # 第一轮 verify_stage 标出 dependency_issue，第二轮清零后通过。
    return _scenario(
        "interface_drift",
        "A Python-to-Verilog interface drift is caught before final admission.",
        [
            {"event": "prompt", "attempt_id": "iface-a1", "stage": "rtl", "subfunction": "load", "budget": "compact"},
            {
                "event": "verify_stage",
                "attempt_id": "iface-a1",
                "ready": False,
                "issues": [{"source": "dependency_issue"}],
            },
            {
                "event": "reflect",
                "attempt_id": "iface-a1",
                "error_sources": ["dependency_issue"],
                "action": "regenerate",
            },
            {"event": "prompt", "attempt_id": "iface-a2", "stage": "rtl", "subfunction": "load", "budget": "repair"},
            {"event": "verify_stage", "attempt_id": "iface-a2", "ready": True, "issues": []},
            {"event": "workflow_attempt", "attempt_id": "iface-a2", "status": "passed"},
        ],
    )

# 人工决策场景覆盖 intervention 后继续 resume 的路径。
def _human_decision_feedback() -> dict[str, Any]:
    """构造人工确认缺失决策后恢复 workflow 的评估场景。

    参数:
        无。

    返回:
        人工决策恢复场景的评估字典。
    """

    # 该场景确保 decision.json 能作为恢复输入进入第二轮验证。
    return _scenario(
        "human_decision_feedback",
        "A missing interface decision blocks, then resume uses the user's decision.",
        [
            {
                "event": "validate",
                "attempt_id": "human-a1",
                "readiness": "static",
                "ok": False,
                "error_sources": ["needs_human_intervention"],
            },
            {"event": "human_intervention", "attempt_id": "human-a1", "primary_source": "needs_human_intervention"},
            {"event": "resume_workflow", "attempt_id": "human-a2", "decision": "decision.json"},
            {"event": "validate", "attempt_id": "human-a2", "readiness": "static", "ok": True, "error_sources": []},
            {"event": "workflow_attempt", "attempt_id": "human-a2", "status": "passed"},
        ],
    )

# 语义输出漂移场景覆盖 reference vector 与 testbench 联合诊断。
def _semantic_output_drift() -> dict[str, Any]:
    """构造 reference vector 输出漂移经诊断后再通过的场景。

    参数:
        无。

    返回:
        语义输出漂移场景的评估字典。
    """

    # 第一轮 execute readiness 失败后，反思阶段要求重新生成。
    return _scenario(
        "semantic_output_drift",
        "Reference-vector mismatch is classified as a testbench/current module issue.",
        [
            {
                "event": "validate",
                "attempt_id": "sem-a1",
                "readiness": "execute",
                "ok": False,
                "error_sources": ["current_module_issue", "testbench_issue"],
            },
            {
                "event": "reflect",
                "attempt_id": "sem-a1",
                "error_sources": ["current_module_issue", "testbench_issue"],
                "action": "regenerate",
            },
            {"event": "validate", "attempt_id": "sem-a2", "readiness": "execute", "ok": True, "error_sources": []},
            {"event": "workflow_attempt", "attempt_id": "sem-a2", "status": "passed"},
        ],
    )
