"""通过 erie-remote-ssh 执行 Verilog skill 远端信心门禁。

机器可读 stdout 协议：`--report-runs` 会在 stdout 末尾输出一个 JSON 对象，供
validate_verilog_skill.py 读取 retained remote run 证据；其他人工可读状态使用
current-project 规定的 `> INFO: [Python]`、`> WARNING: [Python]` 或
`> ERR: [Python]` 前缀。
"""

# 标准库负责 CLI、时间戳和本地功能模块装载。
import argparse
import hashlib

# importlib、JSON 和进程环境负责本地支撑模块装载与协议序列化。
import importlib.util
import json
import os
import sys

# 临时文件、时间戳和 UUID 支撑一次性证据收据写入。
import tempfile
import time
import uuid

# ModuleSpec 提供动态模块 spec 的明确静态类型。
from importlib.machinery import ModuleSpec

# pathlib 负责 skill 根目录、本地支持模块路径和受控 POSIX 相对路径。
from pathlib import Path, PurePosixPath

# types 提供 ModuleType，便于标注本地支持模块对象。
from types import ModuleType

# Any 只用于本地支持模块公开的异构 JSON 载荷。
from typing import Any, cast

# 文本型回归 gate 会直接扫描 facade 源码，因此这里保留固定 VG 目录标识。
STR_RTL_MD_GATE_MARKERS_HEAD = "remote_verilog_quality_gates VG072 VG147"  # 文本型回归 gate 依赖的目录边界标识

# 文本型回归 gate 还会继续核对后半段标识，确保远端约束回归仍被 facade 对外承诺。
STR_RTL_MD_GATE_MARKERS_TAIL = "VG123 eval-skill vg_semantic_gate_regression"  # 文本型回归 gate 依赖的执行标识

# skill 根目录直接关系脚本定位 runtime、配置和示例数据。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # 当前 runtime、scripts 和 config 所在的 skill 根目录

# 仓库根目录用于关联 smoke 目录和兼容旧逻辑的治理数据。
PATH_PROJECT_ROOT = PATH_SKILL_ROOT.parents[1]  # 当前 skill 仓库根目录

# _load_local_support_module 通过文件路径装载本地支撑模块，兼容脚本直跑和动态导入。
def _load_local_support_module(str_file_name: str, str_module_name: str) -> ModuleType:
    """按文件路径装载 remote 子模块。

    :param str_file_name: 同目录支撑模块文件名。
    :param str_module_name: 当前进程注册时使用的临时模块名。
    :return: 已执行完成的模块对象。
    :raises ImportError: 模块 spec 或 loader 缺失时抛出。
    """

    # 目标模块与 facade 位于同一 remote 目录下。
    path_module = Path(__file__).with_name(str_file_name)  # 待装载的本地支撑模块路径

    # spec_from_file_location 负责把文件模块转成当前进程可执行 spec。
    module_type_spec: ModuleSpec | None = cast(  # 本地支撑模块的 import spec
        ModuleSpec | None,  # cast 之后的可空 spec 类型
        importlib.util.spec_from_file_location(str_module_name, path_module),  # 动态模块 spec 工厂调用
    )

    # 缺少 spec 或 loader 说明模块文件无法按当前方式装载。
    if module_type_spec is None or module_type_spec.loader is None:

        # 把模块路径写进错误文本，方便快速定位缺失文件。
        raise ImportError(f"> ERR: [Python] failed to load local support module: {path_module}")

    # 先创建未执行模块对象，再交给 loader 执行源码。
    module_type_obj_module: ModuleType = importlib.util.module_from_spec(module_type_spec)  # 尚未执行源码的本地支撑模块对象

    # dataclass introspection 需要在 sys.modules 里能找到当前模块名。
    sys.modules[str_module_name] = module_type_obj_module  # 先注册模块对象，供 dataclass introspection 找回当前模块

    # 执行模块源码，把真实函数和常量装入模块对象。
    module_type_spec.loader.exec_module(module_type_obj_module)

    # 返回已执行的支撑模块对象，交给 facade 后续导出和装配逻辑复用。
    return module_type_obj_module

# selection 支撑模块负责 settings 读取、server 选择和 runtime config 装配。
module_type_selection_support = _load_local_support_module(  # facade 绑定 server 选择支撑模块
    "remote_validate_selection.py",  # 承载 settings 与 server 选择逻辑的实现文件
    "readable_verilog_remote_validate_selection",  # 供本地导入注册 selection 模块的名称
)

# staging manifest 必须先注册，供无 package context 的 execution 动态导入。
module_type_stage_manifest_support = _load_local_support_module(  # facade 绑定 staging manifest 支撑模块
    "remote_stage_manifest.py",  # 承载 Git tracked 与无 Git 递归清单逻辑的实现文件
    "readable_verilog_remote_stage_manifest",  # execution 动态导入使用的稳定模块别名
)

# 归档完整性模块先载入，供归档验证片段使用。
module_type_archive_integrity_support = _load_local_support_module(  # facade 绑定归档完整性支撑模块
    "remote_archive_integrity.py",  # 承载归档解包、manifest 和摘要校验脚本生成逻辑
    "readable_verilog_remote_archive_integrity",  # 归档验证模块别名
)

# reports 处置模块先载入，供输出策略包装器使用。
module_type_output_cleanup_support = _load_local_support_module(  # facade 绑定输出处置支撑模块
    "remote_output_cleanup.py",  # 承载 retained reports 路径检查脚本生成逻辑
    "readable_verilog_remote_output_cleanup",  # 输出策略模块别名
)

# execution 支撑模块承载 staging、request 和 retained-run 汇总实现。
module_type_execution_support = _load_local_support_module(  # facade 绑定 retained-run 执行支撑模块
    "remote_validate_execution.py",  # 承载 request 执行与 retained-run 汇总逻辑的实现文件
    "readable_verilog_remote_validate_execution",  # 让 loader 在 sys.modules 中标识 retained-run 实现模块
)

# receipt 支撑模块承载 release-gate 收据的本地 canonical 计算。
module_type_receipt_support = _load_local_support_module(  # facade 绑定测试证据收据构建模块
    "test_evidence_receipt.py",  # 收据、Git 树和源码清单实现文件
    "readable_verilog_remote_test_evidence_receipt",  # 供 facade 使用的稳定模块别名
)

# snippets 支撑模块承载 shell 片段、远端路径工具和固定常量。
module_type_snippets_support = _load_local_support_module(  # facade 绑定 shell 片段与路径常量模块
    "remote_validate_snippets.py",  # 承载 shell 片段和远端路径工具的实现文件
    "readable_verilog_remote_validate_snippets",  # 让 loader 以 shell 常量模块名缓存该模块对象
)

# 保留对外读取 SKILL_ROOT 名称，兼容老测试和人工审阅路径。
SKILL_ROOT = PATH_SKILL_ROOT  # 兼容旧脚本节点的 skill 根目录

# 旧 helper 使用 PROJECT_ROOT 关联 smoke 目录和仓库合同。
PROJECT_ROOT = PATH_PROJECT_ROOT  # 兼容旧 helper 的仓库根目录

# new_remote_run_id 生成可抵抗同秒并发碰撞的 retained run 标识。
def new_remote_run_id() -> str:
    """生成带 UTC 时间序列后缀的远程 validation run id。

    :param: 此函数没有外部业务参数。
    :return: ``validation_<UTC>_<time_ns>_<nonce8>`` 安全目录标识。
    """

    # UTC 时间片保留人工可排序性，明确使用 Z 标记避免误读本地时区。
    str_timestamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())  # UTC 人工可读时间片

    # 随机因子隔离不同进程、主机和 checkout 在同一时刻发起的验证。
    str_nonce = uuid.uuid4().hex[:8]  # retained run 的随机碰撞隔离因子

    # 组合字段只使用字母、数字和下划线，保持远端相对路径安全。
    return f"validation_{str_timestamp}_{time.time_ns()}_{str_nonce}"

# _export_module_members 让 facade 按旧名字暴露子模块成员，同时避免一长串逐行别名赋值。
def _export_module_members(obj_source_module: ModuleType, str_member_names: str) -> None:
    """按空格分隔的名称表把子模块成员导出到 facade 全局命名空间。

    :param obj_source_module: 提供真实实现的本地支撑模块对象。
    :param str_member_names: 以空格分隔的旧入口名字列表。
    :return: 不返回业务值，只更新 facade 模块级符号。
    """

    # 逐个提取子模块成员，保证旧入口名字继续绑定到 facade 模块对象上。
    for str_member_name in str_member_names.split():

        # 直接把旧入口名字映射回当前模块全局，保持旧测试和 monkeypatch 访问面不变。
        globals()[str_member_name] = getattr(obj_source_module, str_member_name)  # 同步旧入口名字到 facade 全局命名空间

# facade 先导出 retained-run 常量，供 CLI 和摘要逻辑按旧名字直接读取。
_export_module_members(
    module_type_snippets_support,
    "WORKFLOW_CLI_MODULE REMOTE_FIXTURES REMOTE_EXECUTE_ROOT REMOTE_FIXTURE_ROOT "
    "REMOTE_EXECUTE_VALIDATION_JSON REMOTE_EXECUTE_RTL_PATH REMOTE_EXECUTE_TESTBENCH_PATH "
    "REMOTE_FIXTURE_SUMMARY_JSON REMOTE_PYTEST_SUMMARY_JSON "
    "REMOTE_PYTEST_TARGETED_SUMMARY_JSON REMOTE_PYTEST_REGRESSION_SUMMARY_JSON "
    "REMOTE_PYTEST_FULL_SUMMARY_JSON REMOTE_ENVIRONMENT_JSON REMOTE_CWD_JSON "
    "REMOTE_PRESSURE_REPORT_JSON REMOTE_ARCHIVE_MANIFEST_JSON REMOTE_TEST_EVIDENCE_JSON "
    "REMOTE_AGENT_REVIEW_JSON SIMULATOR_BACKENDS",
)

# facade 导出 selection dataclass，保留 isinstance 和旧 helper 注入入口。
_export_module_members(module_type_selection_support, "RemoteHelperContext RemoteValidationRunConfig")

# facade 导出 settings 与 server 选择 helper，保持 CLI 装配和 selection 回放接口不变。
_export_module_members(
    module_type_selection_support,
    "load_settings remote_setting remote_runtime_settings_relpath load_remote_runtime_config "
    "resolve_confirmed_remote_server require_workspace_root resolve_server resolve_server_from_selection "
    "resolve_local_remote_runtime_config resolve_server_list_path selection_from_args "
    "build_remote_runtime_config_payload write_remote_runtime_config require_remote_relative_path "
    "require_remote_absolute_file_path",
)

# facade 导出远端 shell 片段 helper，保持命令拼装和路径工具入口稳定。
_export_module_members(
    module_type_snippets_support,
    "remote_validation_command rtl_md_constraint_remote_snippet remote_output_cleanup_snippet "
    "remote_bytecode_cleanup_snippet simulator_priority_export_snippet vivado_activation_snippet "
    "remote_validation_transport_command remote_join sh_quote REMOTE_COMPLETION_JSON",
)

# facade 导出 execution helper，保持 staging、JSON 协议和 retained-run 摘要入口不变。
_export_module_members(
    module_type_execution_support,
    "ensure_local_prerequisites ensure_remote_prerequisites ensure_remote_read_prerequisites "
    "PACKAGE_ARCHIVE_NAME stage_package write_package_manifest package_archive_path "
    "create_package_archive cleanup_package cleanup_package_archive remove_tree_with_retries "
    "request_and_run helper_base run_helper "
    "emit_prefixed_lines emit_json_payload parse_request_path cleanup_requests cleanup_local_residuals "
    "summarize_validation_report summarize_fixture_report summarize_pytest_report "
    "parse_json_output parse_download_path",
)

# facade 导出收据构建器，避免运行时依赖外部 agents-md-generator。
_export_module_members(
    module_type_receipt_support,
    "canonical_receipt_sha256 build_test_evidence_receipt",
)

# retained 摘要路径表集中定义，summary facade 只复制后传给 execution 层。
REMOTE_SUMMARY_PATHS = {  # 单轮远程报告需要下载或定位的证据相对路径
    "completion_json": str(REMOTE_COMPLETION_JSON),  # 最终完成身份清单
    "execute_validation_json": str(REMOTE_EXECUTE_VALIDATION_JSON),  # 主流程验证 JSON
    "execute_rtl_path": str(REMOTE_EXECUTE_RTL_PATH),  # 生成 RTL 人工复核入口
    "execute_testbench_path": str(REMOTE_EXECUTE_TESTBENCH_PATH),  # testbench 失败回放入口
    "fixture_summary_json": str(REMOTE_FIXTURE_SUMMARY_JSON),  # 固定远程夹具汇总
    "pytest_summary_json": str(REMOTE_PYTEST_SUMMARY_JSON),  # 权威远程 pytest 摘要
    "pytest_targeted_summary_json": str(REMOTE_PYTEST_TARGETED_SUMMARY_JSON),  # targeted 阶段机器摘要
    "pytest_regression_summary_json": str(REMOTE_PYTEST_REGRESSION_SUMMARY_JSON),  # regression 行为族摘要
    "pytest_full_summary_json": str(REMOTE_PYTEST_FULL_SUMMARY_JSON),  # full 测试树摘要
    "environment_json": str(REMOTE_ENVIRONMENT_JSON),  # 远端环境事实
    "cwd_json": str(REMOTE_CWD_JSON),  # 远端目录事实
    "pressure_report_json": str(REMOTE_PRESSURE_REPORT_JSON),  # 技能压力报告
    "archive_manifest_json": str(REMOTE_ARCHIVE_MANIFEST_JSON),  # 验证归档清单
    "test_evidence_json": str(REMOTE_TEST_EVIDENCE_JSON),  # 远端测试总表
    "agent_review_json": str(REMOTE_AGENT_REVIEW_JSON),  # outer run 根的 Agent 审核
}

# build_parser 负责组装 CLI 合同，避免 main 承担参数细节。
def build_parser() -> argparse.ArgumentParser:
    """构造远端验证脚本的命令行解析器。

    :param: 此函数没有外部业务参数。
    :return: 已注册远端验证、报告查询和工具链选择参数的解析器。
    """

    # 创建描述远端信心门禁职责的解析器。
    parser = argparse.ArgumentParser(  # 远端验证 CLI 参数解析器
        description="Validate this skill on a configured remote SSH server.",  # CLI 帮助中的脚本用途说明
    )

    # settings 缺省为 skill 自带 defaults.json。
    parser.add_argument("--settings", type=Path, default=PATH_SKILL_ROOT / "config" / "defaults.json")

    # server 参数只覆盖本次运行，不写入项目选择状态。
    parser.add_argument("--server", help="Override configured server id/name.")

    # keep-remote 是兼容选项；当前默认行为已经保留远端目录。
    parser.add_argument(
        "--keep-remote",
        action="store_true",
        help="Compatibility option; remote validation directories are kept by default.",
    )

    # cleanup-remote 只有用户明确要求时才删除远端验证目录。
    parser.add_argument(
        "--cleanup-remote",
        action="store_true",
        help="Delete the remote validation directory after the gate finishes.",
    )

    # report-runs 仅读取保留运行证据，不创建新的远端验证目录。
    parser.add_argument(
        "--report-runs",
        action="store_true",
        help="List retained remote validation runs without staging a new run.",
    )

    # max-runs 控制 retained run 报告大小，避免枚举过多历史目录。
    parser.add_argument("--max-runs", type=int, default=5, help="Maximum retained runs to include with --report-runs.")

    # run-id 让自动化调用方只读取刚完成的 outer run，避免并发时误取其他证据。
    parser.add_argument("--run-id", help="Read one exact retained run with --report-runs.")

    # write-test-evidence 将确切 retained run 转成 release-gate 收据。
    parser.add_argument(
        "--write-test-evidence",
        type=Path,
        help="Write an agents-md-generator-compatible test evidence receipt from --run-id.",
    )

    # TESTER 提交必须显式绑定，禁止用当前产品提交替代测试提交。
    parser.add_argument(
        "--test-commit-sha",
        help="TESTER commit SHA to bind into --write-test-evidence.",
    )

    # toolchain-config 兼容调用方传入本地 .settings/verilog.remote.json 副本。
    parser.add_argument(
        "--toolchain-config",
        type=Path,
        help="Compatibility option for the local copy of .settings/verilog.remote.json.",
    )

    # write-toolchain-selection 将用户确认的工具链选择写入本地和远端配置。
    parser.add_argument(
        "--write-toolchain-selection",
        action="store_true",
        help="Write a confirmed remote toolchain choice to the project-local config and exit.",
    )

    # simulator-backend 只允许 runtime 已支持的后端名称。
    parser.add_argument(
        "--simulator-backend",
        choices=SIMULATOR_BACKENDS,
        help="Confirmed simulator backend for --write-toolchain-selection.",
    )

    # xsim 需要 settings64.sh 路径；非 xsim 后端可不传。
    parser.add_argument("--vivado-settings", help="Confirmed remote Vivado settings64.sh path for xsim.")

    # 返回 parser 供 main 或测试使用。
    return parser

# main 编排三种入口：写工具链选择、报告 retained runs、执行远端验证。
# _validate_fixed_remote_root 固定新验证写入根，拒绝旧目录漂移。
def _validate_fixed_remote_root(dict_settings: dict[str, Any]) -> str:
    """校验并返回固定的远端验证根目录。

    :param dict_settings: 已加载的 readable-verilog-generator 配置。
    :return: 固定为 .readable-verilog-generator 的远端根目录。
    :raises ValueError: settings 声明其他远端根时抛出。
    """

    # 读取配置声明，保留 require helper 对相对路径的安全校验。
    str_configured_remote_root = require_remote_relative_path(  # settings 声明的远端根目录
        remote_setting(dict_settings, "remote_root"),  # 读取 remote_root 配置值
        "settings.remote.remote_root",  # 错误消息中的配置字段名
    )

    # 旧根只读兼容，新写入必须固定在用户指定的根目录。
    if str_configured_remote_root != ".readable-verilog-generator":

        # 配置漂移时 fail closed，避免在错误远端位置产生不可追溯报告。
        raise ValueError(
            "> ERR: [Python] settings.remote.remote_root must be .readable-verilog-generator."
        )

    # 返回单一写入边界，后续函数不再重复信任配置文本。
    return ".readable-verilog-generator"

# _resolve_cli_runtime 读取设置并组装远端验证所需的稳定上下文。
def _resolve_cli_runtime(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> dict[str, Any]:
    """解析 CLI settings、远端上下文和固定路径。

    :param args: argparse 解析得到的 CLI 命名空间。
    :param parser: 当前 parser，用于报告服务器选择错误。
    :return: 远端上下文、本地配置路径、远端根和运行参数映射。
    """

    # settings 载荷决定 helper、服务器、超时和远端 Python。
    dict_settings = load_settings(args.settings)  # readable-verilog-generator 治理配置

    # 组装 erie-remote-ssh 调用上下文，后续请求复用同一对象。
    remote_helper_context_cli = build_remote_context(  # helper 连接上下文
        dict_settings,  # 已解析的 skill settings
        args,  # 当前 CLI 参数命名空间
        parser,  # server 选择错误的 parser
    )

    # 找到本地 runtime 配置副本，供工具链选择写入使用。
    path_local_runtime_config = resolve_local_remote_runtime_config(  # 本地 runtime JSON 路径
        dict_settings,  # 提供 workspace_root 元数据的配置
        args.toolchain_config,  # 兼容 CLI 传入的本地路径
    )

    # 校验远端根并固定后续所有写入路径的锚点。
    str_remote_root = _validate_fixed_remote_root(dict_settings)  # .readable-verilog-generator 远端写入边界

    # runtime 配置必须位于固定远端根内，禁止落到 server default workdir 其他位置。
    str_remote_runtime_config = require_remote_relative_path(  # 远端 runtime 配置路径
        remote_join(  # 拼接固定根和 runtime 相对路径
            str_remote_root,  # 固定远端验证根
            remote_setting(dict_settings, "remote_runtime_config"),  # settings 中的 runtime 相对路径
        ),  # remote runtime 配置的完整相对路径
        "settings.remote.remote_runtime_config",  # runtime 配置错误提示字段
    )

    # 远端 Python 命令仍按 settings 选择，保持现有兼容接口。
    str_remote_python = remote_setting(dict_settings, "python")  # 远端执行 Python 命令

    # 返回主编排所需的具名上下文和路径合同。
    return {
        "remote_context": remote_helper_context_cli,
        "local_runtime_config": path_local_runtime_config,
        "remote_root": str_remote_root,
        "remote_runtime_config": str_remote_runtime_config,
        "remote_python": str_remote_python,
    }

# _handle_toolchain_selection 处理用户确认后的工具链配置同步分支。
def _handle_toolchain_selection(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    dict_runtime: dict[str, Any],
) -> int | None:
    """写入本地并上传确认的远端工具链配置。

    :param args: argparse 解析得到的 CLI 命名空间。
    :param parser: 当前 parser，用于检查工具链参数完整性。
    :param dict_runtime: resolve_cli_runtime 返回的运行上下文。
    :return: 该分支执行时返回 0，否则返回 None 交给后续分派。
    """

    # 非工具链同步调用继续执行其他 CLI 分支。
    if not args.write_toolchain_selection:

        # 当前命令没有写配置请求，不在此处分派。
        return None

    # 从 CLI 参数构造已经确认的工具链选择。
    dict_selection = selection_from_args(args, parser)  # 用户确认的工具链选择载荷

    # 转换为 runtime/verilog.remote.json 的标准结构。
    dict_payload = build_remote_runtime_config_payload(dict_selection)  # 持久化 runtime 配置载荷

    # 本地副本先写入，保证后续验证使用同一配置事实。
    write_remote_runtime_config(  # 写入项目本地 runtime 配置
        dict_runtime["local_runtime_config"],  # 本地 .settings 配置路径
        dict_payload,  # 已规范化的工具链 JSON
    )

    # 上传前先确认 helper、settings 和 server list 完整。
    ensure_local_prerequisites(dict_runtime["remote_context"])

    # 写配置模式需要远端连接、扫描和 workspace 预检。
    ensure_remote_prerequisites(dict_runtime["remote_context"])

    # 将确认配置上传到固定远端 workdir 的 runtime 路径。
    upload_remote_runtime_config(  # 上传远端 verilog.remote.json
        dict_runtime["remote_context"],  # 已验证的 erie-remote-ssh 上下文
        dict_payload,  # 用户确认的 runtime 配置
        dict_runtime["remote_runtime_config"],  # 固定根内的 runtime 路径
    )

    # 输出短状态，机器协议只保留必要选择字段。
    print("> INFO: [Python] remote runtime selection synced.")

    # 兼容旧 CLI 的机器可读尾部 JSON。
    emit_json_payload(  # 输出 server 和 toolchain 选择摘要
        {
            "server": dict_runtime["remote_context"].str_server,
            **dict_payload["remote"]["toolchain"],
        }
    )

    # 配置同步分支完成后不继续执行完整远端 gate。
    return 0

# _handle_report_mode 读取 retained run 报告并输出机器协议。
def _handle_report_mode(
    args: argparse.Namespace,
    dict_runtime: dict[str, Any],
) -> int | None:
    """执行只读 retained-run 报告分支。

    :param args: argparse 解析得到的 CLI 命名空间。
    :param dict_runtime: resolve_cli_runtime 返回的运行上下文。
    :return: 报告分支执行时返回 0，否则返回 None。
    """

    # report-runs 与 write-test-evidence 互斥，避免重复消费同一证据。
    if not args.report_runs or args.write_test_evidence:

        # 当前调用不是普通报告模式。
        return None

    # 报告模式只要求本地 helper 和配置文件可读。
    ensure_local_prerequisites(dict_runtime["remote_context"])

    # 只读预检确认 server_1 和 workspace 当前可达。
    ensure_remote_read_prerequisites(dict_runtime["remote_context"])

    # 读取指定窗口内的 retained run 机器摘要。
    dict_report = report_remote_runs(  # report-runs 的机器协议载荷
        dict_runtime["remote_context"],  # 已验证服务器上下文
        dict_runtime["remote_root"],  # 固定远端根目录
        args.max_runs,  # 调用方允许读取的最近运行数
        exact_run_id=args.run_id,  # 可选的精确 outer run 标识
    )

    # stdout 末尾保留 JSON，供上层 parse_json_object 读取。
    emit_json_payload(dict_report)

    # 报告分支完成后返回成功码。
    return 0

# _require_evidence_arguments 验证收据分支必需的显式绑定。
def _require_evidence_arguments(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """验证 write-test-evidence 的 run 和 TESTER 提交参数。

    :param args: argparse 解析得到的 CLI 命名空间。
    :param parser: 当前 parser，用于报告缺少的绑定字段。
    :return: 参数完整时不返回业务值。
    """

    # 收据必须绑定精确 outer run。
    if not args.run_id:

        # parser.error 形成稳定的 CLI 错误协议。
        parser.error("--write-test-evidence requires --run-id")

    # 收据必须绑定 TESTER 提交 SHA。
    if not args.test_commit_sha:

        # parser.error 阻止生成无法追溯测试树的收据。
        parser.error("--write-test-evidence requires --test-commit-sha")

# _load_exact_remote_evidence 读取单个 retained run 的远端测试事实。
def _load_exact_remote_evidence(
    args: argparse.Namespace,
    dict_runtime: dict[str, Any],
) -> dict[str, Any]:
    """下载并验证精确 retained run 的测试事实。

    :param args: argparse 解析得到的 CLI 命名空间。
    :param dict_runtime: resolve_cli_runtime 返回的运行上下文。
    :return: 远端 runtime 写出的 canonical 测试事实。
    :raises RuntimeError: 精确 run 或远端测试事实缺失时抛出。
    """

    # 本地 helper、固定远端根和精确 run 都必须可读。
    ensure_local_prerequisites(dict_runtime["remote_context"])

    # 远端读取预检确认当前服务器和 workspace 可访问。
    ensure_remote_read_prerequisites(dict_runtime["remote_context"])

    # 只查询用户指定的一个 retained run，避免历史结果混入收据。
    dict_report = report_remote_runs(  # 精确 retained run 的报告载荷
        dict_runtime["remote_context"],  # 已验证的远程连接上下文
        dict_runtime["remote_root"],  # 固定 retained 根目录
        1,  # 收据只允许一个目标 run
        exact_run_id=args.run_id,  # 用户指定的 outer run 身份
    )

    # 解析报告中返回的精确 retained 列表。
    list_runs = dict_report.get("runs", [])  # 当前精确 run 的报告列表

    # 多个或零个 run 都不能形成确定性收据。
    if len(list_runs) != 1:

        # 缺失或歧义 run 必须停止收据写入。
        raise RuntimeError("> ERR: [Python] exact retained run evidence is unavailable")

    # 读取远端测试事实分组，拒绝 wrapper 自己伪造的计数。
    dict_test_evidence_group = list_runs[0].get("test_evidence", {})  # retained 测试证据分组

    # 只接受远端 runtime 写出的 canonical 测试事实。
    dict_remote_evidence = (  # 远端 runtime canonical 测试事实
        dict_test_evidence_group.get("remote")  # 远端 runtime 原始总表
        if isinstance(dict_test_evidence_group, dict)  # 仅接受字典分组
        else None  # 缺失分组按不可用处理
    )

    # 缺少远端 runtime 事实时拒绝生成收据。
    if not isinstance(dict_remote_evidence, dict):

        # wrapper 汇总不能替代远程实际测试证据。
        raise RuntimeError("> ERR: [Python] remote test evidence is unavailable")

    # 返回已经通过结构验证的远端事实。
    return dict_remote_evidence

# _write_test_evidence_receipt 生成本地 canonical 测试收据。
def _write_test_evidence_receipt(
    args: argparse.Namespace,
    dict_remote_evidence: dict[str, Any],
) -> int:
    """把远端测试事实写为本地 canonical 收据。

    :param args: argparse 解析得到的 CLI 命名空间。
    :param dict_remote_evidence: 已验证的远端 runtime 测试事实。
    :return: 收据写入成功时返回 0。
    """

    # builder 校验和收据输出都以仓库根为路径锚点。
    path_project = PATH_PROJECT_ROOT.resolve()  # 收据验证使用的仓库根

    # 相对输出路径固定解释在仓库根目录内。
    path_output = (  # 最终 test-evidence 收据路径
        args.write_test_evidence  # CLI 给出的绝对或相对输出路径
        if args.write_test_evidence.is_absolute()  # 判断绝对路径输入
        else path_project / args.write_test_evidence  # 相对路径锚定仓库根
    ).resolve()

    # 创建一次性 JSON 输入文件，复用 canonical builder 的统一读取入口。
    int_temporary_fd, str_temporary_path = tempfile.mkstemp(  # 远端测试事实临时文件
        prefix="remote-test-evidence-",  # 临时文件名前缀
        suffix=".json",  # canonical builder 读取的文件后缀
    )

    # 关闭底层描述符，后续由 Path 写入 JSON。
    os.close(int_temporary_fd)

    # Path 对象承载临时文件的确定性清理路径。
    path_temporary = Path(str_temporary_path)  # 一次性远端测试事实路径

    # 先写远端事实，再调用 canonical builder。
    try:

        # JSON 排序和 UTF-8 编码保持跨平台字节稳定。
        path_temporary.write_text(  # 写入 canonical builder 临时输入
            json.dumps(dict_remote_evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n",  # 稳定 JSON 文本
            encoding="utf-8",  # 固定 UTF-8 编码
        )

        # builder 同时校验 TESTER 提交祖先关系和源码/测试树绑定。
        dict_receipt = build_test_evidence_receipt(  # canonical 收据结果
            PATH_PROJECT_ROOT.resolve(),  # 当前产品源树
            path_temporary,  # 远端 runtime 事实文件
            path_output,  # 最终收据输出路径
            args.test_commit_sha,  # TESTER 提交身份
        )

    # 临时 JSON 无论成功或失败都必须删除。
    finally:

        # 一次性输入不是 retained 证据，不能留在仓库或系统临时目录。
        path_temporary.unlink(missing_ok=True)

    # 输出收据路径和摘要，避免打印完整 JSON 正文。
    emit_json_payload(  # 输出机器可读的收据摘要
        {
            "status": "passed",
            "receipt_path": str(path_output),
            "receipt_sha256": dict_receipt["receipt_sha256"],
        }
    )

    # 收据写入成功。
    return 0

# _handle_test_evidence_mode 处理 write-test-evidence 分支。
def _handle_test_evidence_mode(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    dict_runtime: dict[str, Any],
) -> int | None:
    """处理测试收据模式并保持精确 run 绑定。

    :param args: argparse 解析得到的 CLI 命名空间。
    :param parser: 当前 parser，用于验证显式绑定。
    :param dict_runtime: resolve_cli_runtime 返回的运行上下文。
    :return: 收据模式执行时返回 0，否则返回 None。
    """

    # 没有收据参数时交给默认远端验证流程。
    if not args.write_test_evidence:

        # 当前调用不是收据分支。
        return None

    # 先验证 run id 和 TESTER 提交绑定。
    _require_evidence_arguments(args, parser)

    # 读取并验证远端 canonical 测试事实。
    dict_remote_evidence = _load_exact_remote_evidence(  # 精确 retained run 的 canonical 测试事实
        args,  # 当前 CLI 绑定参数
        dict_runtime,  # 已解析的远端运行上下文
    )

    # 将远端事实写为本地 canonical 收据。
    return _write_test_evidence_receipt(args, dict_remote_evidence)

# main 编排 CLI 入口和三类业务分支。
def main(argv: list[str] | None = None) -> int:
    """执行远端 Verilog skill 信心门禁 CLI。

    :param argv: argparse 解析得到的 CLI 命名空间。
    :return: 进程退出码，0 表示目标操作成功完成。
    :raises AssertionError: 当 settings 字段不合法且 parser.error 返回时抛出。
    """

    # 构造 parser 后再解析参数。
    parser = build_parser()  # 远端验证 CLI 解析器

    # 解析调用方传入的参数。
    args = parser.parse_args(argv)  # 解析后的 CLI 参数

    # 解析 settings、固定根和 helper 上下文。
    try:

        # 运行时上下文统一由 helper 解析，main 不重复拼接路径。
        dict_runtime = _resolve_cli_runtime(args, parser)  # 远端验证运行上下文

    # settings 字段不合法时转成 argparse 风格错误。
    except ValueError as exc:

        # parser.error 打印 usage 并以退出码 2 结束。
        parser.error(str(exc))

        # 静态类型需要显式标记该分支不可继续。
        raise AssertionError("> ERR: [Python] unreachable argparse error branch.") from exc

    # 工具链同步分支优先结束。
    int_toolchain_result = _handle_toolchain_selection(args, parser, dict_runtime)  # 工具链分支结果

    # 已同步工具链时立即返回，避免继续执行远端验证。
    if int_toolchain_result is not None:

        # 该分支已经输出同步状态。
        return int_toolchain_result

    # 普通 report-runs 查询分支只读取 retained 证据。
    int_report_result = _handle_report_mode(args, dict_runtime)  # 报告分支结果

    # report-runs 已输出机器协议时不再进入默认 gate。
    if int_report_result is not None:

        # 该分支已经输出机器协议 JSON。
        return int_report_result

    # write-test-evidence 分支必须在默认 gate 之前完成。
    int_evidence_result = _handle_test_evidence_mode(args, parser, dict_runtime)  # 收据分支结果

    # 收据已经落盘时结束 CLI，保持精确 run 绑定。
    if int_evidence_result is not None:

        # 该分支已经写出收据并输出摘要。
        return int_evidence_result

    # 未命中早退分支时执行完整远端验证。
    return run_remote_validation(
        dict_runtime["remote_context"],  # 沿用已通过预解析的 helper 连接对象
        dict_runtime["remote_root"],  # 新验证固定使用的 retained 根目录
        dict_runtime["remote_python"],  # settings 选择的远端 Python 入口
        dict_runtime["remote_runtime_config"],  # 固定根内持久化 runtime 配置
        cleanup_remote=cleanup_remote_requested(args),  # 显式 cleanup 开关
    )

# build_remote_context 从 settings 和 CLI 解析 helper 所需的本地路径。
def build_remote_context(
    dict_settings: dict[str, Any],
    namespace_args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> RemoteHelperContext:
    """解析 erie-remote-ssh helper 的调用上下文。

    :param dict_settings: 已加载的 Verilog skill settings 字典。
    :param namespace_args: CLI 参数解析结果。
    :param parser: 当前 CLI parser，用于报告缺少服务器选择。
    :return: 包含 helper、settings、server list、server 和 timeout 的上下文。
    """

    # helper 路径来自项目 settings 的 remote 配置。
    path_helper = Path(remote_setting(dict_settings, "helper"))  # 远端 request helper 脚本路径

    # remote settings 是 erie-remote-ssh 自身的配置文件。
    path_remote_settings = Path(remote_setting(dict_settings, "settings"))  # helper 自身 settings 文件路径

    # server list 路径可能由 settings 指向项目本地私有文件。
    path_server_list = resolve_server_list_path(Path(remote_setting(dict_settings, "server_list")))  # 服务器清单路径

    # server 由命令行覆盖或项目已确认选择提供。
    str_server = resolve_server(dict_settings, namespace_args.server, parser)  # 远端目标服务器标识

    # 完整 pytest、effectiveness 和真实仿真共用有限的十分钟执行窗口。
    int_timeout = int(dict_settings.get("remote", {}).get("timeout_s", 600))  # 远端请求超时秒数

    # 返回上下文对象，后续 helper 函数不再重复传五个基础参数。
    return RemoteHelperContext(
        path_helper=path_helper,
        path_remote_settings=path_remote_settings,
        path_server_list=path_server_list,
        str_server=str_server,
        int_timeout=int_timeout,
    )

# run_remote_validation 执行默认远端打包、上传、命令和可选清理流程。
def staged_source_digest(path_package_root: Path) -> str:
    """计算 staging 目录的稳定 SHA-256 内容摘要。

    :param path_package_root: 已完成 staging 的本地目录根。
    :return: 由排序后的相对路径和文件字节共同决定的十六进制摘要。
    """

    # 同时纳入路径和内容，避免同字节文件在不同位置产生相同包身份。
    obj_digest: object = hashlib.sha256()  # 当前 staging 包的增量 SHA-256 计算器

    # 排序消除枚举顺序差异，保证跨运行可复现。
    for path_file in sorted(
        (path_item for path_item in path_package_root.rglob("*") if path_item.is_file()),
        key=lambda path_item: path_item.relative_to(path_package_root).as_posix(),
    ):

        # 相对路径使用 POSIX 分隔符，消除主机差异。
        str_relative_path = path_file.relative_to(path_package_root).as_posix()  # 摘要中的平台无关相对路径

        # 路径先进入摘要，避免相同字节位于不同位置时产生同一包身份。
        obj_digest.update(str_relative_path.encode("utf-8"))

        # 空字节分隔路径和内容，阻止字段拼接歧义。
        obj_digest.update(b"\0")

        # 文件原始字节保证任何 staging 内容变化都会改变摘要。
        obj_digest.update(path_file.read_bytes())

        # 文件尾分隔符阻止相邻文件字节边界产生拼接歧义。
        obj_digest.update(b"\0")

    # 十六进制形式便于写入 JSON、环境变量和发布证据。
    return obj_digest.hexdigest()

# 默认远端流程把 staging 身份、上传请求和 completion 证据绑定为一轮运行。
def run_remote_validation(
    remote_context: RemoteHelperContext,
    str_remote_root: str,
    str_remote_python: str,
    str_remote_runtime_config: str,
    *,
    cleanup_remote: bool,
) -> int:
    """执行完整远端信心门禁。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_root: 远端 retained run 根目录。
    :param str_remote_python: 远端 Python 命令。
    :param str_remote_runtime_config: 远端 runtime 配置相对路径。
    :param cleanup_remote: 是否在 gate 后删除远端验证目录。
    :return: 进程退出码，0 表示远端 gate 成功。
    :raises BaseException: staging、归档、远端请求或验证阶段失败时继续抛出原始异常。
    """

    # 每次远端验证都使用高熵 run id，避免同秒并发共享 retained 目录。
    str_run_id = new_remote_run_id()  # 远端 retained run 目录名

    # 远端 outer run 固定位于 remote_root/runs/<validation-id>。
    str_remote_parent = remote_join(str_remote_root, "runs", str_run_id)  # 本次远端 run 目录

    # 上传包放在 run/workspace，避免源码与 reports 直接混在 outer 根。
    str_remote_skill = remote_join(str_remote_parent, "workspace", "readable-verilog-generator")  # 远端 skill 包目录

    # 报告直接位于 outer run/reports，禁止再次嵌套 smoke_runs_*。
    str_remote_reports = remote_join(str_remote_parent, "reports")  # 本次 run 的直接报告目录

    # 打印远端保留位置，便于用户后续 SSH 查看证据。
    for str_line in remote_location_lines(str_remote_parent, str_remote_skill, cleanup_remote):

        # 远端 retained 位置作为短状态写入日志。
        print(f"> INFO: [Python] remote location: {str_line}")

    # 本地 helper 和远端基础环境都必须先通过。
    ensure_local_prerequisites(remote_context)

    # 完整 gate 要求远端软件扫描和 workspace 检查。
    ensure_remote_prerequisites(remote_context)

    # 远端 runtime 配置必须由用户确认过工具链选择后存在。
    dict_remote_runtime = download_remote_runtime_config(  # 完整 gate 启动前下载的工具链配置
        remote_context,  # 完整 gate 使用的 helper 上下文
        str_remote_runtime_config,  # 从远端 workdir 下载的 runtime 配置文件
    )

    # 打包本地 skill 和 smoke 目录到临时 staging 根。
    path_package_root = stage_package(remote_context.path_helper, str_run_id)  # 本地临时上传包根目录

    # 归档或摘要生成失败时立即清理 staging，避免半成品留在本地临时目录。
    try:

        # 将 staging 树封装成单文件，避免递归 SCP 在目录树中静默遗漏模块。
        path_upload_archive = create_package_archive(path_package_root)  # 本轮唯一上传源归档

        # 摘要必须在上传前对最终 staging 计算，后续 completion 与调用方都绑定该值。
        str_source_digest = staged_source_digest(path_package_root)  # 本轮上传包的稳定 SHA-256 身份

    # 归档或摘要阶段发生异常时进入本地临时资源清理分支。
    except BaseException:

        # 失败时清理归档和 staging；原始异常继续交给上层报告。
        cleanup_package_archive(path_package_root)

        # 删除归档来源目录，避免下一轮误用半成品。
        cleanup_package(path_package_root)

        # 保留原始异常类型和堆栈，禁止把失败伪装成成功。
        raise

    # 归档放在远端 skill 目录内，解包目标为其父级 workspace。
    str_remote_archive_name = PACKAGE_ARCHIVE_NAME  # 远端解包使用的归档文件名

    # 请求列表在 try 前初始化，保证失败清理路径也可访问。
    list_request_paths: list[Path] = []  # 本轮创建的 erie-remote-ssh request 文件

    # 把一次 retained run 的执行参数收束，避免下游函数签名膨胀。
    run_config = RemoteValidationRunConfig(  # 本次远端 retained run 执行配置
        path_package_root=path_package_root,  # request-upload 的本地来源
        str_remote_parent=str_remote_parent,  # 远端 retained run 容器
        str_remote_skill=str_remote_skill,  # bash gate 的工作区
        str_remote_python=str_remote_python,  # 远端解释器命令
        bool_cleanup_outputs=cleanup_remote,  # smoke 输出清理开关
        dict_toolchain_selection=dict_remote_runtime["toolchain"],  # Vivado/xsim 选择载荷
        str_remote_runtime_config_path=str_remote_runtime_config,  # 多版本提示用配置路径
        str_run_id=str_run_id,  # completion 与 report-runs 共用的 outer run 身份
        str_source_digest=str_source_digest,  # completion 绑定的上传包身份
        str_remote_server=remote_context.str_server,  # 远端环境指纹绑定的服务器标识
        str_remote_reports=str_remote_reports,  # outer run 的直接报告目录
        path_upload_archive=path_upload_archive,  # 本地 tar.gz 上传源
        str_remote_archive_name=str_remote_archive_name,  # 远端 skill 目录内的归档文件名
    )

    # 远端执行可能失败，但本地 staging 和 request 文件必须进入清理路径。
    try:

        # 远端上传和执行请求集中在小函数里，保持主编排易读。
        list_request_paths = run_remote_validation_requests(  # finally 阶段清理的 request 路径
            remote_context,  # 受控 SSH helper 上下文
            run_config,  # 已收束的 retained run 参数
        )

    # 清理阶段无论远端命令成功与否都要执行。
    finally:

        # 清理本地 staging 和可选远端 retained run。
        finalize_remote_validation_run(
            remote_context,
            run_config,
            list_request_paths,
            cleanup_remote=cleanup_remote,  # 是否删除远端 retained run
        )

    # 远端 gate 全流程通过。
    print("> INFO: [Python] Readable Verilog generator remote confidence gate passed.")

    # stdout 末尾机器协议让父级 validate 精确绑定随后读取的 retained run。
    emit_json_payload(
        {
            "run_id": str_run_id,
            "source_digest": str_source_digest,
            "status": "passed",
        }
    )

    # 成功退出码保持旧 CLI 行为。
    return 0

# _build_outer_run_relative_path 把 workspace 内的安全叶路径映射到 outer run。
def _build_outer_run_relative_path(str_leaf: str) -> str:
    """构造 workspace 到 outer run 子路径的受控相对引用。

    :param str_leaf: outer run 内的安全叶路径。
    :return: 从 workspace skill 根指向 outer run 子路径的 POSIX 引用。
    """

    # 只允许经过 remote_join 校验的叶路径进入受控映射。
    str_normalized_leaf = remote_join(str_leaf)  # outer run 子路径的规范化叶名称

    # 两级父目录只跨越固定的 workspace/readable-verilog-generator 层级。
    path_outer_run = PurePosixPath("..", "..", str_normalized_leaf)  # 固定 outer run 相对路径对象

    # POSIX 序列化结果不接受外部父目录输入，保持报告布局可审计。
    return path_outer_run.as_posix()  # workspace 到 outer run 的稳定相对引用

# run_remote_validation_requests 创建并执行远端验证需要的三个 request。
def run_remote_validation_requests(
    remote_context: RemoteHelperContext,
    run_config: RemoteValidationRunConfig,
) -> list[Path]:
    """上传远端验证包并执行完整远端 gate。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param run_config: 本次远端 retained run 的上传和执行参数。
    :return: 本轮创建的本地 request 文件路径列表。
    """

    # 已创建的 request 文件需要在 finally 中清理。
    list_request_paths: list[Path] = []  # 本轮远端 request 草稿路径

    # 先在远端创建 retained run 父目录。
    list_request_paths.append(
        request_and_run(
            remote_context,
            "request-mkdir",
            ["--path", run_config.str_remote_parent, "--reason", "prepare Verilog skill validation directory"],
        )
    )

    # workspace 子目录承载上传源码，reports 与源码保持同级隔离。
    list_request_paths.append(
        request_and_run(
            remote_context,
            "request-mkdir",
            [
                "--path",
                remote_join(run_config.str_remote_parent, "workspace"),
                "--reason",
                "prepare Verilog skill validation workspace",
            ],
        )
    )

    # 归档上传需要先创建最终 skill 目录，避免 scp 把归档落到错误层级。
    if run_config.path_upload_archive is not None and run_config.str_remote_archive_name:

        # 记录创建归档解包目录的远端请求。
        list_request_paths.append(

            # 通过统一 helper 创建远端目录。
            request_and_run(
                remote_context,
                "request-mkdir",
                [
                    "--path",
                    run_config.str_remote_skill,
                    "--reason",
                    "prepare Verilog skill archive extraction directory",
                ],
            )
        )

    # 优先上传单一归档；旧调用方未提供归档时保留目录上传兼容路径。
    if run_config.path_upload_archive is not None and run_config.str_remote_archive_name:

        # 记录单一归档上传请求。
        list_request_paths.append(

            # 通过统一 helper 上传确定性归档。
            request_and_run(
                remote_context,
                "request-upload",
                [
                    "--local",
                    str(run_config.path_upload_archive),
                    "--remote",
                    remote_join(run_config.str_remote_skill, run_config.str_remote_archive_name),
                    "--reason",
                    "upload Verilog skill validation package archive",
                    "--confirm-sensitive-local-upload",
                ],
                run_request_args=["--confirm-sensitive-local-upload"],
            )
        )

    # 未配置归档时继续使用旧的递归目录上传兼容路径。
    else:

        # 记录兼容目录上传请求。
        list_request_paths.append(

            # 通过统一 helper 上传 staging skill 目录。
            request_and_run(
                remote_context,
                "request-upload",
                [
                    "--local",
                    str(run_config.path_package_root / "readable-verilog-generator"),
                    "--remote",
                    run_config.str_remote_skill,
                    "--reason",
                    "upload Verilog skill validation package",
                    "--confirm-sensitive-local-upload",
                ],
                run_request_args=["--confirm-sensitive-local-upload"],
            )
        )

    # 远端执行命令包含 compile、smoke、fixture 和 readiness 验证。
    # reports 与 workspace skill 根保持两级相对关系，避免硬编码路径散落。
    str_report_root = _build_outer_run_relative_path("reports")  # outer run 的直接 reports 目录

    # Agent 审核文件由权威路径表命名，并与 reports 同级保留。
    str_agent_review_path = _build_outer_run_relative_path(  # outer run 根的审核清单路径
        REMOTE_SUMMARY_PATHS["agent_review_json"],  # 权威审核文件名
    )

    # 依据固定 outer run 目录生成完整远端验证命令。
    str_command = remote_validation_command(  # 远端 bash 验证脚本
        run_config.str_remote_skill,  # bash gate 执行根目录
        run_config.str_remote_python,  # 远端 Python 可执行命令

        # cleanup 只影响本轮 smoke 输出保留策略。
        cleanup_outputs=run_config.bool_cleanup_outputs,  # 远端 smoke 产物保留策略

        # reports 是 outer run 的直接子目录，远程脚本从 workspace skill 相对定位。
        report_root=str_report_root,  # 直接报告目录相对路径

        # Agent 审核文件与 reports 同级，成功和失败都自动保留。
        agent_review_path=str_agent_review_path,  # outer run 根的审核文件

        # 工具链选择来自已经验证过枚举和值域的 runtime 配置。
        toolchain_selection=run_config.dict_toolchain_selection,  # 外部仿真工具链选择

        # 配置路径只用于错误提示和审计定位。
        remote_runtime_config_path=run_config.str_remote_runtime_config_path,  # 失败提示中的持久化路径

        # outer run 身份进入最终 completion 清单。
        run_id=run_config.str_run_id,  # 最终完成清单绑定的 outer run 身份

        # staging 摘要阻止其他源码包复用本轮完成证据。
        source_digest=run_config.str_source_digest,  # completion 绑定的上传包 SHA-256

        # 服务器标识进入远端环境和测试证据，不写入连接凭据。
        remote_server_id=run_config.str_remote_server,  # 本轮远端服务器身份

        # 归档文件名用于远端解包和逐文件完整性校验。
        package_archive_name=run_config.str_remote_archive_name,  # skill 目录内的 tar.gz 名称
    )

    # 将完整 bash 正文压缩后再进入 request，避免 Windows OpenSSH argv 超过上限。
    str_transport_command = remote_validation_transport_command(  # request-command 的短传输正文
        str_command,  # 保持报告、fixture 和 Agent 审核逻辑不变的完整 bash 正文
        run_config.str_remote_python,  # 远端 Python 负责解压并执行原始正文
        str_report_root=str_report_root,  # request 审计中保留直接 reports 路径
        str_agent_review_path=str_agent_review_path,  # request 审计中保留 Agent 审核路径
    )

    # 创建并执行远端 command request。
    list_request_paths.append(
        request_and_run(
            remote_context,
            "request-command",
            [
                "--reason",
                "run Verilog skill remote confidence gate",
                "--",
                "bash",
                "-lc",
                str_transport_command,
            ],
        )
    )

    # 返回 request 路径供 finally 统一清理。
    return list_request_paths

# finalize_remote_validation_run 清理本地 staging 并处理远端 retained 策略。
def finalize_remote_validation_run(
    remote_context: RemoteHelperContext,
    run_config: RemoteValidationRunConfig,
    list_request_paths: list[Path],
    *,
    cleanup_remote: bool,
) -> None:
    """完成远端验证后的清理和 retained 证据提示。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param run_config: 本次远端 retained run 的上传和执行参数。
    :param list_request_paths: 需要清理的本地 request 文件路径。
    :param cleanup_remote: 是否删除远端 retained run。
    :return: 不返回业务值；清理失败仅作为 warning 暴露。
    """

    # cleanup-remote 只在用户显式要求时删除远端 retained run。
    if cleanup_remote:

        # 删除请求失败时只报告 warning，不吞掉主流程异常。
        cleanup_remote_validation_run(remote_context, run_config.str_remote_parent)

    # 默认 retained 策略下报告远端证据目录。
    else:

        # 详细 retained 路径已由 remote_location_lines 在启动阶段打印。
        print("> INFO: [Python] remote validation artifacts retained.")

    # 先清理 staging 同级的确定性归档，避免 reports/tmp 持续增长。
    cleanup_package_archive(run_config.path_package_root)

    # 再清理归档来源目录。
    cleanup_package(run_config.path_package_root)

    # 删除本地 request 文件，远端执行证据仍留在 retained run 中。
    cleanup_requests(list_request_paths)

    # 清理本地 skill 目录下可能由导入或测试产生的 pycache。
    cleanup_local_residuals()

# cleanup_remote_validation_run 删除用户显式要求清理的远端 retained run。
def cleanup_remote_validation_run(remote_context: RemoteHelperContext, str_remote_parent: str) -> None:
    """删除远端 retained run 目录。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_parent: 本次远端 retained run 父目录。
    :return: 不返回业务值；删除失败仅打印 warning。
    """

    # 删除 retained run 属于可选清理动作，失败时只降级为 warning。
    try:

        # 通过 erie-remote-ssh request-delete 删除整个 run 目录。
        request_and_run(
            remote_context,
            "request-delete",
            [
                "--path",
                str_remote_parent,
                "--recursive",
                "--reason",
                "cleanup Verilog skill validation directory",
            ],
        )

    # 远端清理失败不改变已完成验证的主结果，但必须显式暴露。
    except Exception as exc:

        # stderr 使用 WARNING 前缀，保留清理失败上下文。
        print(f"> WARNING: [Python] remote cleanup failed: {exc}", file=sys.stderr)

# cleanup_remote_requested 保留 --keep-remote 兼容语义。
def cleanup_remote_requested(namespace_args: argparse.Namespace) -> bool:
    """判断用户是否明确要求删除远端验证目录。

    :param namespace_args: CLI 参数解析结果。
    :return: True 表示 gate 结束后请求删除远端 retained run。
    """

    # 只有 cleanup_remote 为真时删除，keep_remote 不改变默认保留策略。
    return bool(getattr(namespace_args, "cleanup_remote", False))

# remote_location_lines 生成远端 retained run 的短状态行。
def remote_location_lines(str_remote_parent: str, str_remote_skill: str, cleanup_remote: bool) -> list[str]:
    """生成远端验证目录位置摘要。

    :param str_remote_parent: 本次远端 retained run 父目录。
    :param str_remote_skill: 上传后的远端 skill 目录。
    :param cleanup_remote: 是否计划在结束后删除 retained run。
    :return: 三行短状态，供 CLI 和测试断言复用。
    """

    # 该列表是公开测试合同，顺序保持不变。
    return [
        f"remote_parent: {str_remote_parent}",
        f"remote_skill: {str_remote_skill}",
        f"remote_cleanup_requested: {cleanup_remote}",
    ]

# upload_remote_runtime_config 通过 execution 模块实现同步远端 runtime 配置。
def upload_remote_runtime_config(
    remote_context: RemoteHelperContext,
    dict_payload: dict[str, Any],
    str_remote_runtime_config: str,
) -> None:
    """上传 verilog.remote.json 到远端工作目录。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param dict_payload: 待上传的 runtime 配置载荷。
    :param str_remote_runtime_config: 远端 workdir 内的配置相对路径。
    :return: 不返回业务值；上传请求执行完成即表示配置已同步。
    """

    # execution 模块负责 request 上传与临时副本清理，facade 只注入本地写文件 helper。
    module_type_execution_support.upload_remote_runtime_config(
        remote_context,
        dict_payload,
        str_remote_runtime_config,
        func_write_remote_runtime_config=write_remote_runtime_config,
    )

# download_remote_runtime_config 通过 execution 模块实现下载并解析远端 runtime 配置。
def download_remote_runtime_config(
    remote_context: RemoteHelperContext,
    str_remote_runtime_config: str,
) -> dict[str, Any]:
    """下载远端 workdir 中的 verilog.remote.json。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_runtime_config: 远端 runtime 配置相对路径。
    :return: 解析后的 runtime 配置字典。
    """

    # execution 模块负责下载和缺失处理，facade 只注入本地 JSON 解析 helper。
    return module_type_execution_support.download_remote_runtime_config(
        remote_context,
        str_remote_runtime_config,
        func_load_remote_runtime_config=load_remote_runtime_config,
    )

# report_remote_runs 汇总远端 retained run 证据。
def report_remote_runs(*args: Any, exact_run_id: str | None = None) -> dict[str, Any]:
    """读取远端 retained run 列表并下载摘要证据。

    :param args: 新入口为 `(RemoteHelperContext, remote_root, max_runs)`；旧入口为
        `(helper, settings, server_list, server, remote_root, max_runs)`。
    :param exact_run_id: 可选的确切 outer run 目录名；提供后禁止 latest 枚举。
    :return: 包含 remote_root、runs 和 status 的报告字典。
    :raises TypeError: 参数数量或首参类型不符合兼容入口时抛出。
    """

    # 新入口已经由 build_remote_context 收敛参数，直接复用内部实现。
    if len(args) == 3 and isinstance(args[0], RemoteHelperContext):

        # 三参入口来自当前 CLI --report-runs 主路径。
        return _report_remote_runs_with_context(
            args[0],
            str(args[1]),
            int(args[2]),
            exact_run_id=exact_run_id,
        )

    # 旧 smoke 和外部脚本仍传入 helper/settings/server-list/server/root/max-runs。
    if len(args) == 6:

        # 旧入口没有 timeout 参数，沿用 dataclass 默认语义中的普通命令超时。
        remote_helper_context_legacy = RemoteHelperContext(  # 兼容旧六参入口时临时组装出的远端 helper 上下文
            path_helper=Path(args[0]),  # 旧入口传入的 helper 脚本路径
            path_remote_settings=Path(args[1]),  # 旧入口传入的远端配置路径
            path_server_list=Path(args[2]),  # 旧入口传入的 server-list 路径
            str_server=str(args[3]),  # 旧入口选择的目标服务器名
            int_timeout=120,  # 旧入口沿用的普通命令超时秒数
        )

        # 旧入口的后两项对应远端 retained 根和最大 run 数。
        return _report_remote_runs_with_context(
            remote_helper_context_legacy,
            str(args[4]),
            int(args[5]),
            exact_run_id=exact_run_id,
        )

    # 参数不匹配时给出明确错误，避免下游 unpack 报错难定位。
    raise TypeError("> ERR: [Python] report_remote_runs 只接受 3 个 context 参数或 6 个 legacy 参数")

# _report_remote_runs_with_context 承载 report_remote_runs 的实际实现。
def _report_remote_runs_with_context(
    remote_context: RemoteHelperContext,
    str_remote_root: str,
    int_max_runs: int,
    *,
    exact_run_id: str | None = None,
) -> dict[str, Any]:
    """读取远端 retained run 列表并汇总摘要证据。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_root: 远端 retained run 根目录。
    :param int_max_runs: 最多返回的运行条目数量。
    :param exact_run_id: 可选的确切 outer run 目录名。
    :return: 包含 remote_root、runs 和 status 的稳定字典。
    """

    # execution 模块负责 retained-run 汇总逻辑，facade 只传递当前 helper 回调字典和目标根目录。
    return module_type_execution_support.report_remote_runs_with_context_impl(
        remote_context,
        str_remote_root,
        int_max_runs,
        exact_run_id=exact_run_id,
        dict_dependencies={
            "helper_base": helper_base,
            "run_helper": run_helper,
            "parse_json_output": parse_json_output,
            "remote_join": remote_join,
            "summarize_remote_run": summarize_remote_run,
        },
    )

# summarize_remote_run 下载单个 retained run 的关键报告。
def summarize_remote_run(
    remote_context: RemoteHelperContext,
    str_remote_root: str,
    str_run_name: str,
) -> dict[str, Any]:
    """汇总单个 retained remote run 的执行和 fixture 证据。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_root: 远端 retained run 根目录。
    :param str_run_name: 当前 run-* 目录名。
    :return: 包含 remote_execute 和 fixtures 摘要的字典。
    """

    # execution 模块负责 retained-run 摘要拼装，facade 只传递 helper 回调和远端证据路径字典。
    return module_type_execution_support.summarize_remote_run_impl(
        remote_context,
        str_remote_root,
        str_run_name,
        dict_dependencies={
            "remote_join": remote_join,
            "helper_base": helper_base,
            "run_helper": run_helper,
            "parse_json_output": parse_json_output,
        } | {
            "download_json_optional": download_json_optional,
            "summarize_validation_report": summarize_validation_report,
            "summarize_fixture_report": summarize_fixture_report,
            "summarize_pytest_report": summarize_pytest_report,
        },
        dict_remote_paths=dict(REMOTE_SUMMARY_PATHS),  # 本轮摘要读取的稳定证据路径副本
    )

# download_json_optional 下载远端 JSON 文件，失败时返回 None。
def download_json_optional(
    remote_context: RemoteHelperContext,
    str_remote_path: str,
    str_local_path: str,
) -> dict[str, Any] | None:
    """下载远端 JSON 证据，失败或缺失时返回 None。

    :param remote_context: erie-remote-ssh helper 调用上下文。
    :param str_remote_path: 远端 JSON 文件绝对路径。
    :param str_local_path: 本地下载目标路径。
    :return: 成功时返回 JSON 字典；失败或缺失时返回 None。
    """

    # execution 模块负责远端 JSON 下载和缺失回退，facade 只传递 helper 回调字典。
    return module_type_execution_support.download_json_optional_impl(
        remote_context,
        str_remote_path,
        str_local_path,
        dict_dependencies={
            "helper_base": helper_base,
            "run_helper": run_helper,
            "parse_download_path": parse_download_path,
        },
    )

# 脚本模块只在直接执行时调用 main。
if __name__ == "__main__":

    # 将 main 的退出码交给解释器。
    sys.exit(main())
