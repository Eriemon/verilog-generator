"""管理可选文档注册化生命周期；标准输出为单个 JSON 对象协议。"""

# 延迟注解求值保持 CLI 与受支持 Python 版本兼容。
from __future__ import annotations

# 标准库负责命令行、JSON 协议、路径和进程退出状态。
import argparse
import json
from pathlib import Path
import sys
from typing import Any

# 同目录共享模块提供文档扫描、启用状态与初始化能力。
from .document_registry_common import (
    document_governance_status,
    finalize_document_governance,
    initialize_document_governance,
    scan_skill_documents,
    validate_document_governance,
)

# 参数解析器公开首个只读 scan 子命令。
def parse_args(list_arguments: list[str] | None = None) -> argparse.Namespace:
    """解析文档注册化控制器参数。

    参数：list_arguments 为可选显式参数列表；为空时读取进程参数。
    返回：包含子命令、技能目录和输出协议选项的命名空间。
    """

    # 顶层解析器描述控制器的可选治理职责。
    object_parser = argparse.ArgumentParser(  # 文档注册化顶层参数解析器。
        description="Manage optional document registry governance.",  # 控制器公开用途。
    )

    # 子命令集合要求调用方明确选择生命周期阶段。
    object_subparsers = object_parser.add_subparsers(dest="command", required=True)  # 生命周期子命令解析器。

    # scan 子命令只读取技能文档并输出候选事实。
    object_scan_parser = object_subparsers.add_parser("scan", help="Scan managed skill documents.")  # 扫描参数解析器。

    # 技能根决定 SKILL.md 与 references 的扫描边界。
    object_scan_parser.add_argument("skill_dir")

    # JSON 选项保留与其他 registry CLI 一致的显式机器协议入口。
    object_scan_parser.add_argument("--json", action="store_true", dest="bool_json")

    # init 子命令要求启用和写入两个显式信号共同授权。
    object_init_parser = object_subparsers.add_parser("init", help="Initialize optional document governance.")  # 初始化参数解析器。

    # 初始化目标技能根与扫描边界一致。
    object_init_parser.add_argument("skill_dir")

    # --enable 记录用户明确选择可选门禁。
    object_init_parser.add_argument("--enable", action="store_true", dest="bool_enable")

    # --write 单独授权创建配置与草案文件。
    object_init_parser.add_argument("--write", action="store_true", dest="bool_write")

    # 初始化结果始终支持单对象 JSON 协议。
    object_init_parser.add_argument("--json", action="store_true", dest="bool_json")

    # check 和 status 都不得隐式启用未配置技能。
    for str_command_name in ("check", "status"):

        # 两个只读命令共享技能根和机器输出参数。
        object_status_parser = object_subparsers.add_parser(  # 当前只读状态命令解析器。
            str_command_name,  # check 或 status 公开动作名。
            help=f"Read optional document governance {str_command_name} state.",  # 当前动作帮助文本。
        )

        # 状态读取目标技能根。
        object_status_parser.add_argument("skill_dir")

        # JSON 参数保持生命周期 CLI 一致。
        object_status_parser.add_argument("--json", action="store_true", dest="bool_json")

    # finalize 只在 Agent 完成草案复核后晋级持久状态。
    object_finalize_parser = object_subparsers.add_parser(  # 完成阶段参数解析器。
        "finalize",  # 公开完成动作名。
        help="Finalize an Agent-reviewed document registry draft.",  # 完成动作帮助文本。
    )

    # 完成目标必须是已经显式启用的技能根。
    object_finalize_parser.add_argument("skill_dir")

    # 写入信号防止只读验证静默晋级状态。
    object_finalize_parser.add_argument("--write", action="store_true", dest="bool_write")

    # 仅 Agent 标记不确定时要求用户通过本开关明确确认。
    object_finalize_parser.add_argument("--confirm-user", action="store_true", dest="bool_confirm_user")

    # 完成结果使用单对象机器协议。
    object_finalize_parser.add_argument("--json", action="store_true", dest="bool_json")

    # 完整命名空间交给主入口分派。
    return object_parser.parse_args(list_arguments)

# JSON 输出器保证 stdout 只有一个机器可读对象。
def emit_json(dict_payload: dict[str, Any]) -> None:
    """把结构化结果写入显式机器协议标准输出。

    参数：dict_payload 为当前命令的完整结果对象。
    返回：无业务返回值，副作用是输出单个 JSON 对象。
    """

    # 尾随换行便于调用方按行读取唯一 JSON 对象。
    sys.stdout.write(json.dumps(dict_payload, ensure_ascii=False, sort_keys=True) + "\n")

# 主入口把公开子命令映射到共享实现并稳定处理错误。
def main(list_arguments: list[str] | None = None) -> int:
    """执行文档注册化控制器并返回进程退出码。

    参数：list_arguments 为可选测试参数列表；为空时读取进程参数。
    返回：零表示成功，二表示请求或输入错误。
    """

    # 先解析命令，确保 argparse 保持标准帮助与请求错误行为。
    namespace_args = parse_args(list_arguments)  # 当前文档注册化请求参数。

    # scan 阶段只收集事实，不要求技能预先启用门禁。
    if namespace_args.command == "scan":

        # 用户路径在共享扫描器中接受目录边界校验。
        path_skill_root = Path(namespace_args.skill_dir).resolve()  # 规范化技能根路径。

        # 输入错误映射为稳定 JSON，而不是泄漏 Python 回溯。
        try:

            # 扫描结果包含文档、重复和脚本接口候选事实。
            dict_payload = scan_skill_documents(path_skill_root)  # 当前技能文档扫描报告。

        # 只捕获共享扫描器明确声明的请求错误。
        except ValueError as object_error:

            # 错误协议保留可定位正文并使用非零退出码。
            emit_json({"ok": False, "error": str(object_error)})

            # 请求错误与数据库错误使用不同退出状态。
            return 2

        # 成功结果通过显式 JSON 协议写入标准输出。
        emit_json(dict_payload)

        # scan 完成且没有未处理输入错误。
        return 0

    # 所有其他生命周期命令也先规范化技能根。
    path_skill_root = Path(namespace_args.skill_dir).resolve()  # 当前生命周期目标技能根。

    # init 必须同时获得显式启用和写入授权。
    if namespace_args.command == "init":

        # 任一信号缺失都不得创建配置目录。
        if not namespace_args.bool_enable or not namespace_args.bool_write:

            # 错误文本直接说明两个必需开关。
            emit_json(
                {
                    "ok": False,
                    "error": "> ERR: [Python] init requires explicit --enable and --write",
                }
            )

            # finalize 未获得写权限时使用请求错误状态。
            return 2

        # 初始化领域错误转换为稳定请求错误载荷。
        try:

            # 初始化器只写入待 Agent 复核草案。
            dict_payload = initialize_document_governance(path_skill_root)  # 初始化结果载荷。

        # 无效技能根或已配置状态不能泄漏回溯。
        except (OSError, ValueError) as object_error:

            # 结构化错误供自动化调用方定位。
            emit_json({"ok": False, "error": str(object_error)})

            # 初始化输入或状态冲突使用请求错误退出码。
            return 2

        # 授权初始化成功后输出草案文件清单。
        emit_json(dict_payload)

        # 初始化成功不表示 finalize 已完成。
        return 0

    # finalize 只接受显式写入授权，并复用共享完整性门禁。
    if namespace_args.command == "finalize":

        # 缺少 --write 时不得改变草案状态。
        if not namespace_args.bool_write:

            # 错误提示明确说明缺失授权。
            emit_json({"ok": False, "error": "> ERR: [Python] finalize requires explicit --write"})

            # 授权不足属于请求错误。
            return 2

        # 完成过程把领域和文件错误转换为稳定门禁失败。
        try:

            # 用户确认只在本次命令显式给出时传入。
            dict_payload = finalize_document_governance(  # 文档治理完成结果。
                path_skill_root,  # 已完成 Agent 复核的技能根。
                bool_user_confirmed=namespace_args.bool_confirm_user,  # 本次用户确认信号。
            )

        # 未完成复核、正文漂移或文件错误都阻止晋级。
        except (OSError, ValueError, json.JSONDecodeError) as object_error:

            # 单对象错误协议保留具体门禁原因。
            emit_json({"ok": False, "error": str(object_error)})

            # 持久治理未满足使用门禁失败退出码。
            return 3

        # 完成成功后公开 current 状态。
        emit_json(dict_payload)

        # 全部受管文件已晋级。
        return 0

    # check 和 status 都以配置文件作为唯一启用来源。
    if namespace_args.command in {"check", "status"}:

        # 配置解析错误映射为门禁不可用状态。
        try:

            # 状态读取不会创建或修改任何文件。
            dict_status = document_governance_status(path_skill_root)  # 当前可选门禁状态。

        # 非法 JSON 配置需要用户或 Agent 修复。
        except (OSError, ValueError, json.JSONDecodeError) as object_error:

            # 状态错误保留机器可读协议。
            emit_json({"ok": False, "error": str(object_error)})

            # 已配置但不可读属于门禁失败。
            return 3

        # 未启用技能成功跳过条件门禁，且不产生副作用。
        if not dict_status["enabled"]:

            # skipped 明确区分于已启用且检查通过。
            emit_json({"ok": True, "enabled": False, "skipped": True, **dict_status})

            # 可选门禁未启用不是失败。
            return 0

        # status 只公开持久配置状态，不执行昂贵扫描门禁。
        dict_governance = dict_status["governance"]  # 已启用治理配置。

        # status 和 check 在此分流，保持状态读取轻量。
        if namespace_args.command == "status":

            # 状态查询不把 draft 视为命令失败。
            emit_json(
                {
                    "ok": True,
                    "enabled": True,
                    "skipped": False,
                    "status": dict_governance.get("status", "unknown"),
                    "config": dict_status["config"],
                }
            )

            # 只读状态查询成功完成。
            return 0

        # check 对已启用技能执行职责、裁决和漂移完整门禁。
        try:

            # 完成态是持续门禁通过的必要条件。
            dict_documents = validate_document_governance(  # 已验证治理文件集合。
                path_skill_root,  # 当前已启用技能根。
                bool_require_current=True,  # 持续门禁只接受完成态。
            )

        # 任一持久治理错误映射为门禁失败。
        except (OSError, ValueError, json.JSONDecodeError) as object_error:

            # 错误协议帮助调用方决定重新 scan 或修订注册源。
            emit_json({"ok": False, "enabled": True, "skipped": False, "error": str(object_error)})

            # 已启用门禁失败必须阻断标准验证链。
            return 3

        # 完成且当前的技能公开受管记录计数。
        emit_json(
            {
                "ok": True,
                "enabled": True,
                "skipped": False,
                "status": "current",
                "config": dict_status["config"],
                "document_count": len(dict_documents["catalog"]["documents"]),
                "knowledge_count": len(dict_documents["knowledge"]["records"]),
            }
        )

        # 可读配置当前视为状态读取成功。
        return 0

    # required 子命令使该分支只承担静态类型完整性。
    emit_json({"ok": False, "error": "> ERR: [Python] unsupported command"})

    # 未支持命令属于请求错误。
    return 2

# 直接执行脚本时把返回值交给进程退出状态。
if __name__ == "__main__":

    # SystemExit 保留主入口返回的稳定退出码。
    sys.exit(main())
