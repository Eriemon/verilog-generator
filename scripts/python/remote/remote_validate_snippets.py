"""生成远端 shell 片段和路径辅助。"""

# 标准库
import base64
import gzip
import hashlib
import base64
import json
import sys

# 路径类型
from pathlib import Path, PurePosixPath

# 载荷类型
from typing import Any, Mapping

# 支撑模块。
try:
    from .remote_validation_context import normalize_remote_validation_authority, prepare_remote_validation_context
    from .remote_output_cleanup import build_remote_output_cleanup_snippet
    from .remote_validate_gates import (
        filename_gate_remote_snippet as build_filename_gate_remote_snippet,
    )

# 包导入失败时回退。
except ImportError:
    from readable_verilog_remote_validation_context import normalize_remote_validation_authority
    from readable_verilog_remote_validation_context import prepare_remote_validation_context
    from readable_verilog_remote_output_cleanup import build_remote_output_cleanup_snippet
    from readable_verilog_remote_validate_gates import (
        filename_gate_remote_snippet as build_filename_gate_remote_snippet,
    )

# skill 根用于定位 runtime 与 config。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # shell 片段定位根

# 远程验证 authority 由 settings 提供，源码不保存当前案例或布局值。
def _load_remote_validation_authority() -> dict[str, Any]:
    """读取远程验证 authority。

    参数:
        无外部参数；路径由当前 skill 根推导。

    返回:
        远程 validation authority 的独立映射。

    异常:
        ValueError: authority 文件不可读或 validation 段为空。
    """

    # 默认 settings 路径随 skill 安装主体移动。
    path_settings = PATH_SKILL_ROOT / "config" / "defaults.json"  # 读取远程策略的 authority 文件

    # authority 读取失败时阻断远程命令生成。
    try:

        # 解析远程 validation 配置段。
        dict_settings = json.loads(path_settings.read_text(encoding="utf-8"))  # 解析完整 settings 对象

        # 读取远程配置段，供下一步定位 validation authority。
        dict_remote = dict_settings.get("remote", {})  # 远程配置段

        # 只接受对象形式的 validation authority。
        dict_validation = dict_remote.get("validation", {}) if isinstance(dict_remote, dict) else {}  # 提取远程 validation authority

    # 文件或 JSON 错误统一转换为稳定配置异常。
    except (OSError, json.JSONDecodeError) as exc:

        # 配置读取失败时不能生成带隐式值的远程命令。
        raise ValueError("> ERR: [Python] remote validation authority cannot be loaded.") from exc

    # 空 authority 不能安全生成 shell 路径或案例目录。
    if not isinstance(dict_validation, dict) or not dict_validation:

        # 空 authority 无法提供布局、案例和证据路径。
        raise ValueError("> ERR: [Python] remote validation authority is empty.")

    # 返回隔离副本，后续只读使用。
    return dict(dict_validation)

# 读取一次 authority，兼容导出名称继续由配置驱动。
dict_remote_validation_authority = _load_remote_validation_authority()  # 读取远程验证的完整 authority 映射

# 取出案例目录供 fixture 和 precheck 共享。
dict_remote_case_catalog = dict_remote_validation_authority["case_catalog"]  # 案例目录

# 取出布局目录供远程路径常量复用。
dict_remote_layout = dict_remote_validation_authority["layout"]  # 远程布局

# 取出证据文件目录供 retained receipt 复用。
dict_remote_artifacts = dict_remote_validation_authority["artifacts"]  # 证据文件目录

# workflow 执行入口模块。
WORKFLOW_CLI_MODULE = str(dict_remote_validation_authority["workflow_module"])  # workflow 模块路径

# 仿真阶段只使用 manifest 声明的案例集合。
REMOTE_SIMULATION_FIXTURES = tuple(dict_remote_case_catalog["simulation"])  # 仿真案例清单

# retained 阶段使用 manifest 声明的完整案例集合。
REMOTE_FIXTURES = tuple(dict_remote_case_catalog["all"])  # retained 案例清单

# remote execute 证据根由 authority layout 提供。
REMOTE_EXECUTE_ROOT = PurePosixPath(str(dict_remote_layout["execute_root"]))  # authority 主流程证据根

# fixture 汇总根由 authority layout 提供。
REMOTE_FIXTURE_ROOT = PurePosixPath(str(dict_remote_layout["fixture_root"]))  # retained fixture 报告写入的根目录

# remote_pytest_summary.json 保存权威远程 pytest 的精确计数和耗时。
REMOTE_PYTEST_SUMMARY_JSON = PurePosixPath(str(dict_remote_artifacts["pytest_summary"]))  # pytest 总摘要路径

# targeted 阶段摘要保留定向回归的真实命令和计数。
REMOTE_PYTEST_TARGETED_SUMMARY_JSON = PurePosixPath(str(dict_remote_artifacts["pytest_targeted_summary"]))  # targeted 摘要路径

# regression 阶段摘要保留行为族回归的真实命令和计数。
REMOTE_PYTEST_REGRESSION_SUMMARY_JSON = PurePosixPath(str(dict_remote_artifacts["pytest_regression_summary"]))  # 行为族阶段摘要路径

# full 阶段摘要保留完整测试树的真实命令和计数。
REMOTE_PYTEST_FULL_SUMMARY_JSON = PurePosixPath(str(dict_remote_artifacts["pytest_full_summary"]))  # 全量阶段摘要路径

# post-pytest 日志保存 smoke 入口及其真实退出状态，便于定位 pytest 后阶段。
REMOTE_POST_PYTEST_LOG = PurePosixPath(str(dict_remote_artifacts["post_pytest_log"]))  # post-pytest 日志路径

# post-pytest 阶段 JSON 记录当前阶段、状态和真实退出码。
REMOTE_POST_PYTEST_PHASE_JSON = PurePosixPath(str(dict_remote_artifacts["post_pytest_phase"]))  # post-pytest 阶段路径

# 环境文件记录解释器、平台和工具解析事实。
REMOTE_ENVIRONMENT_JSON = PurePosixPath(str(dict_remote_artifacts["environment"]))  # 远端环境事实路径

# cwd 文件记录远程进程的实际工作目录和外层身份。
REMOTE_CWD_JSON = PurePosixPath(str(dict_remote_artifacts["cwd"]))  # 远端目录事实路径

# pressure 文件记录阶段、fixture 和 simulator 覆盖压力。
REMOTE_PRESSURE_REPORT_JSON = PurePosixPath(str(dict_remote_artifacts["pressure"]))  # 压力报告路径

# archive 文件记录 retained 工件的路径、大小和内容摘要。
REMOTE_ARCHIVE_MANIFEST_JSON = PurePosixPath(str(dict_remote_artifacts["archive_manifest"]))  # 归档清单路径

# evidence 文件汇总三阶段和远程身份哈希。
REMOTE_TEST_EVIDENCE_JSON = PurePosixPath(str(dict_remote_artifacts["test_evidence"]))  # 测试证据路径

# completion.json 只在完整远程链到达末尾后原子生成。
REMOTE_COMPLETION_JSON = PurePosixPath(str(dict_remote_artifacts["completion"]))  # 完成清单路径

# agent_review.json 位于 runs/<run-id> 根，记录 Agent 对本轮证据的自动审核结论。
REMOTE_AGENT_REVIEW_JSON = PurePosixPath(str(dict_remote_artifacts["agent_review"]))  # Agent 审核路径

# 旧调用默认把 Agent 审核文件放在 reports 的上一级 outer run 根。
REMOTE_DEFAULT_AGENT_REVIEW_PATH = str(PurePosixPath(str(dict_remote_layout["agent_review_relative"])))  # 兼容旧调用定位审核文件

# validation.json 提供主流程 ok、metrics 和产物映射。
REMOTE_EXECUTE_VALIDATION_JSON = PurePosixPath(str(dict_remote_layout["execute_validation"]))  # 主流程 validation 证据路径

# retained run 的 RTL 复核入口由 authority layout 提供。
REMOTE_EXECUTE_RTL_PATH = PurePosixPath(str(dict_remote_layout["execute_rtl"]))  # 审核器读取的 retained RTL 文件

# retained run 的 testbench 入口由 authority layout 提供。
REMOTE_EXECUTE_TESTBENCH_PATH = PurePosixPath(str(dict_remote_layout["execute_testbench"]))  # 失败复盘读取的 retained testbench 文件

# summary.json 汇总四类远端 fixture 的执行状态。
REMOTE_FIXTURE_SUMMARY_JSON = PurePosixPath(str(dict_remote_layout["fixture_summary"]))  # fixture 汇总 JSON 路径

# simulator 后端枚举必须与 runtime validation 后端名称保持一致。
SIMULATOR_BACKENDS = tuple(dict_remote_validation_authority["simulator_backends"])  # authority 仿真后端

# _ensure_runtime_import_path 只在需要 runtime helper 时调整导入路径。
def _ensure_runtime_import_path() -> None:
    """确保脚本直运行时可导入 skill-local runtime 包。

    :param: 此函数没有外部业务参数。
    :return: 不返回业务值；执行后当前进程可解析 runtime 包。
    """

    # 禁止当前辅助模块在导入 helper 时生成 pyc。
    sys.dont_write_bytecode = True  # 当前进程是否写入 Python 字节码缓存

    # sys.path 用字符串比较，避免 Path 对象与字符串重复插入。
    str_skill_root = str(PATH_SKILL_ROOT)  # runtime 包所在目录的字符串形式

    # 仅在缺少 skill 根目录时补入导入路径。
    if str_skill_root not in sys.path:

        # 脚本直运行时需要把 skill 根目录放到 import 搜索最前面。
        sys.path.insert(0, str_skill_root)

# remote_runtime_settings_relpath 包装 runtime 默认远端配置相对路径。
def remote_runtime_settings_relpath() -> str:
    """返回远端 workdir 中 verilog.remote.json 的默认相对路径。

    :param: 此函数没有外部业务参数。
    :return: 远端 runtime 配置文件的 POSIX 相对路径。
    """

    # 延迟导入避免模块加载阶段触碰 skill-local runtime。
    _ensure_runtime_import_path()

    # runtime 配置模块保存当前 skill 的远端 runtime 配置路径约定。
    from scripts.python.workflow.config import (
        remote_runtime_settings_relpath as func_remote_runtime_settings_relpath,
    )

    # 返回调用方用于提示用户持久化工具链选择的路径。
    return func_remote_runtime_settings_relpath()

# _build_remote_pytest_commands 固化三阶段远程回归命令文本。
def _build_remote_pytest_commands(str_py: str) -> dict[str, str]:
    """返回 targeted、regression、full 三阶段命令。

    :param str_py: 经过 shell quoting 的远端 Python 命令。
    :return: 三阶段命令文本映射。
    """

    # targeted 只覆盖本轮直接变更模块，缩短局部反馈路径。
    str_targeted = (
        f"{str_py} -m pytest -q -p no:cacheprovider "
        "tests/quality/test_formatter_gate.py "
        "tests/quality/test_formatter_refactor.py "
        "tests/quality/test_vg_comb_bindings.py "
        "tests/remote/test_confidence_state.py "
        "tests/workflow/test_checkpoint_closure.py "
        "tests/workflow/test_optimize_assist_flow.py"
    )

    # regression 覆盖质量、验证、工作流和远程行为族。
    str_regression = (
        f"{str_py} -m pytest -q -p no:cacheprovider "
        "tests/quality tests/validation tests/workflow tests/remote"
    )  # 质量、验证、工作流和远程行为族 regression 命令

    # full 复用整个 tests 树，形成最终覆盖基线。
    str_full = f"{str_py} -m pytest -q -p no:cacheprovider"  # 完整测试树 full 命令

    # 具名映射避免阶段命令位置参数错配。
    return {"targeted": str_targeted, "regression": str_regression, "full": str_full}

# _build_remote_phase_runner 生成写入阶段 JSON 摘要的 bash 函数。
def _build_remote_phase_runner(str_py: str) -> str:
    """返回保留 pytest 真实退出码的 bash 阶段函数。

    :param str_py: 经过 shell quoting 的远端 Python 命令。
    :return: 可嵌入主 bash 脚本的阶段执行函数文本。
    """

    # 阶段函数把 pytest 退出状态和 retained 文件绑定成一份可复核证据。
    str_phase_runner = f"""
# 远端阶段函数把 pytest 退出状态和 retained 文件绑定成一份可复核证据。
run_pytest_phase() {{

  # 阶段名称用于区分 targeted、regression 和 full 的 retained 证据。
  local phase_name="$1"

  # 原始命令文本同时用于执行和命令摘要绑定。
  local command_text="$2"

  # 日志路径固定在当前 retained smoke run 下，避免写出工作区边界。
  local file_path_log="$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_pytest_${{phase_name}}.log"

  # 摘要路径与日志共用阶段命名，供汇总器读取真实计数。
  local path_summary="$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_pytest_${{phase_name}}_summary.json"

  # 暂时关闭 errexit，让 pytest 的真实退出码进入结构化摘要。
  set +e

  # pytest 不能继承 outer run 身份，避免测试 fixture 把固定样例写回 retained 审核路径。
  env \\
    -u VERILOG_GENERATOR_REPORT_ROOT \\
    -u VERILOG_GENERATOR_RUN_ROOT \\
    -u VERILOG_GENERATOR_AGENT_REVIEW_PATH \\
    -u VERILOG_GENERATOR_RUN_ID \\
    -u VERILOG_GENERATOR_SOURCE_DIGEST \\
    -u {dict_remote_validation_authority['remote_identity_env']} \\
    -u VERILOG_GENERATOR_SMOKE_RUN_DIR \\
    -u VERILOG_GENERATOR_STARTED_AT \\
    bash -lc "$command_text" 2>&1 | tee "$file_path_log" | tee "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_pytest.log"

  # PIPESTATUS[0] 是 pytest 子进程的真实退出码，而不是 tee 的状态。
  local exit_code_phase="${{PIPESTATUS[0]}}"

  # 后续 shell 步骤恢复严格失败传播。
  set -e

  # 对实际执行的命令文本计算稳定摘要，防止报告脱离执行内容。
  local digest_phase="$(printf '%s' "$command_text" | sha256sum | awk '{{print $1}}')"

  # 记录 UTC 时间戳，支持阶段顺序和远端证据时间线复核。
  local str_timestamp_phase="$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)"

  # 该模块把 pytest 日志、命令摘要和真实退出码固化到阶段 JSON，供 retained run 复核。
  {str_py} -m scripts.python.remote.remote_pytest_summary \\
    --phase "$phase_name" \\
    --log-path "$file_path_log" \\
    --output-path "$path_summary" \\
    --command-hash "$digest_phase" \\
    --exit-code "$exit_code_phase" \\
    --timestamp "$str_timestamp_phase"

  # 阶段失败必须阻断后续阶段，避免生成不完整的完成清单。
  if [ "$exit_code_phase" -ne 0 ]; then

    # 原样返回 pytest 退出码，供远端 request 记录失败原因。
    return "$exit_code_phase"
  fi
}}
""".strip()

    # 返回可嵌入远端主命令体的阶段函数文本。
    return str_phase_runner

# ensure_manifest_only_options 拒绝历史 archive 上传参数。
def ensure_manifest_only_options(dict_options: dict[str, Any]) -> None:
    """校验远程命令选项只能选择 manifest-bound directory upload。

    :param dict_options: 远程命令的兼容关键字载荷。
    :return: 不返回业务值；选项通过时结束。
    :raises ValueError: 发现非空历史 archive 参数时抛出。
    """

    # 归档参数只保留兼容读取，不能改变当前上传合同。
    str_archive_name = str(dict_options.get("package_archive_name", "")).strip()  # 历史 archive 参数

    # 非空归档名表示调用方试图绕过逐文件 source manifest。
    if str_archive_name:

        # 执行层拒绝所有 archive/tar 上传，不提供兼容降级路径。
        raise ValueError(
            "> ERR: [Python] archive upload is disabled; use manifest-bound directory upload"
        )

# _normalize_remote_validation_authority 保留兼容公开入口。
def _normalize_remote_validation_authority(dict_input: Mapping[str, Any]) -> dict[str, Any]:
    """归一化 authority，并复用独立上下文 helper。

    参数:
        dict_input: 调用方传入的 settings、remote 或 validation 映射。
    返回:
        canonical remote validation authority 映射。
    """

    # bundled authority 由模块级配置提供默认布局与案例。
    return normalize_remote_validation_authority(dict_input, dict_remote_validation_authority)

# _prepare_remote_validation_context 提供父模块兼容入口。
def _prepare_remote_validation_context(
    str_remote_skill: str,
    str_remote_python: str,
    dict_options: dict[str, Any],
) -> dict[str, Any]:
    """准备远端 bash 上下文，并把具体实现委托给独立 helper。

    参数:
        str_remote_skill: 远端上传后的 skill 工作区路径。
        str_remote_python: 远端 Python 命令。
        dict_options: 兼容旧关键词的远程执行选项。
    返回:
        remote_validation_command 使用的上下文映射。
    """

    # 回调映射由本模块提供，避免 helper 反向导入生成器。
    dict_helpers = {  # 绑定本函数向独立上下文 helper 提供的全部回调，保证路径与片段生成共享本地策略
        "ensure_manifest_only_options": ensure_manifest_only_options,  # 校验逐文件上传合同并拒绝 archive 降级
        "sh_quote": sh_quote,  # 为远端 shell 参数提供单引号转义
        "remote_output_cleanup_snippet": remote_output_cleanup_snippet,  # 生成 smoke 输出保留与清理脚本
        "simulator_priority_export_snippet": simulator_priority_export_snippet,  # 生成 authority 后端优先级脚本
        "vivado_activation_snippet": vivado_activation_snippet,  # 生成 authority toolchain 激活脚本
        "rtl_md_constraint_remote_snippet": rtl_md_constraint_remote_snippet,  # 生成 RTL 文档约束回归脚本
        "filename_gate_remote_snippet": filename_gate_remote_snippet,  # 生成文件名和 testbench 交付门禁脚本
        "remote_bytecode_cleanup_snippet": remote_bytecode_cleanup_snippet,  # 生成 retained workspace 缓存清理脚本
        "build_remote_pytest_commands": _build_remote_pytest_commands,  # 生成三阶段 pytest 命令映射
        "build_remote_phase_runner": _build_remote_phase_runner,  # 生成阶段摘要和退出码写入脚本
    }  # helper 回调映射

    # 独立 helper 只接收 authority 和显式回调，保持输出字段兼容。
    return prepare_remote_validation_context(
        str_remote_skill,
        str_remote_python,
        dict_options,
        dict_bundled_authority=dict_remote_validation_authority,
        dict_helpers=dict_helpers,
    )

# remote_validation_command 拼装远端 bash 验证脚本。
def remote_validation_command(
    str_remote_skill: str,
    str_remote_python: str,
    **dict_options: Any,
) -> str:
    """生成远端执行的 bash 信心门禁脚本。

    :param str_remote_skill: 远端上传后的 skill 工作区路径。
    :param str_remote_python: 远端 Python 命令。
    :param dict_options: 兼容旧关键词的运行选项，包含 cleanup_outputs、toolchain_selection、
        remote_runtime_config_path、run_id、source_digest 和 authority 身份字段。
    :return: 可交给 `bash -lc` 执行的脚本文本。
    """

    # 统一解析选项并生成命令片段，主函数只负责拼装最终输出。
    dict_context = _prepare_remote_validation_context(  # 解析选项并生成远端主命令上下文
        str_remote_skill,  # 远端 skill workspace 路径
        str_remote_python,  # 远端 Python 解释器命令
        dict_options,  # cleanup、toolchain、run/source 和报告路径选项
    )

    # 主命令正文仍使用本地 quoting 后的 Python 命令。
    str_py = dict_context["str_py"]  # shell quoting 后的远端 Python 命令

    # 先生成不含完成清单的主命令体，命令摘要以此稳定文本为准。
    str_command_body = f"""
set -eu
set -o pipefail
cd {sh_quote(dict_context['str_remote_skill'])}
export VERILOG_GENERATOR_REPORT_ROOT={dict_context['str_report_root_quoted']}
export VERILOG_GENERATOR_RUN_ROOT="$(dirname "$VERILOG_GENERATOR_REPORT_ROOT")"
export VERILOG_GENERATOR_AGENT_REVIEW_PATH={dict_context['str_agent_review_path_quoted']}
export VERILOG_GENERATOR_REMOTE_ROOT={dict_context['str_remote_root']}
export VERILOG_GENERATOR_WORKSPACE_ROOT={dict_context['str_workspace_root']}
export VERILOG_GENERATOR_REPORTS_ROOT={dict_context['str_reports_root']}
export VERILOG_GENERATOR_CASE_CATALOG_PATH={dict_context['str_case_catalog_path']}
export VERILOG_GENERATOR_COMPLETION_PATH={dict_context['str_completion_path']}
export VERILOG_GENERATOR_AGENT_REVIEW_FILE={dict_context['str_agent_review_file']}
export VERILOG_GENERATOR_PYTEST_SUMMARY_PATH={dict_context['str_pytest_summary_path']}
export HOME="$VERILOG_GENERATOR_REPORT_ROOT/.validation-home"
export PYTHONPATH="skills/readable-verilog-generator${{PYTHONPATH:+:$PYTHONPATH}}"
export VERILOG_GENERATOR_WORKFLOW_MODULE={dict_context['str_workflow_module_quoted']}

# outer run 身份用于把远端所有阶段证据绑定到同一 retained 目录。
export VERILOG_GENERATOR_RUN_ID={sh_quote(dict_context['str_run_id'])}

# 上传包源码摘要用于阻止不同候选复用本轮完成清单。
export VERILOG_GENERATOR_SOURCE_DIGEST={sh_quote(dict_context['str_source_digest'])}

# 服务器标识写入远端事实，便于区分多主机验证结果。
export {dict_context['str_identity_env']}={sh_quote(dict_context['str_remote_identity'])}

# reports 目录直接承载日志、阶段摘要和后续结构化证据，不再嵌套 smoke_runs_*。
export VERILOG_GENERATOR_SMOKE_RUN_DIR="$VERILOG_GENERATOR_REPORT_ROOT"

# UTC 起始时间用于排序 completion 和 retained 工件的事件线。
export VERILOG_GENERATOR_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR"

# pytest 后阶段证据只在三轮 pytest 全部完成后启用，避免把 pytest 失败误写成 smoke 失败。
is_post_pytest_phase_enabled=0

# 当前 pytest 后阶段名称供失败退出钩子写入可定位的 retained 标记。
name_post_pytest_phase="post_pytest"

# post-pytest 阶段标记和日志固定绑定当前 outer reports 根。
path_post_pytest_phase="$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_post_pytest_phase.json"
path_post_pytest_log="$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_post_pytest.log"

# 当前 outer run 的审核文件只能由本轮命令生成，不能沿用上传包或预置目录中的旧身份。
rm -f "$VERILOG_GENERATOR_AGENT_REVIEW_PATH"

# 无论阶段在哪一步失败，都由 Agent 生成 retained 审核文件，避免失败证据只剩散落日志。
write_agent_review_on_exit() {{
  local exit_code="$?"
  if [ "${{is_post_pytest_phase_enabled:-0}}" -eq 1 ] && [ "$exit_code" -ne 0 ]; then
    printf '{{"phase":"%s","status":"failed","exit_code":%s,"timestamp":"%s"}}\n' \\
      "$name_post_pytest_phase" "$exit_code" "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)" \\
      > "$path_post_pytest_phase"
  fi
  if [ ! -f "$VERILOG_GENERATOR_AGENT_REVIEW_PATH" ]; then
    REVIEW_STATUS="failed" REVIEW_EXIT_CODE="$exit_code" {str_py} - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

review_path = Path(os.environ["VERILOG_GENERATOR_AGENT_REVIEW_PATH"])
identity_field = "{dict_context['str_identity_field']}"
identity_value = os.environ.get("{dict_context['str_identity_env']}", "")
payload = {{
    "schema": 1,
    "kind": "agent-review",
    "status": os.environ.get("REVIEW_STATUS", "failed"),
    "reviewed_by": "agent",
    "exit_code": int(os.environ.get("REVIEW_EXIT_CODE", "1")),
    "run_id": os.environ.get("VERILOG_GENERATOR_RUN_ID", ""),
    "source_digest": os.environ.get("VERILOG_GENERATOR_SOURCE_DIGEST", ""),
    identity_field: identity_value,
    "reviewed_at": datetime.now(timezone.utc).isoformat(),
}}
review_path.parent.mkdir(parents=True, exist_ok=True)
review_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
PY
  fi
  exit "$exit_code"
}}
trap write_agent_review_on_exit EXIT

# erie-remote-ssh 已在执行 request 前完成 source manifest 校验；此处直接使用已提交 workspace。

# 上传包把隔离 Codex 事实放在 skill 下，启动前复制到 outer reports 的 HOME 映射。
validation_home_source="$PWD/reports/.validation-home"
validation_home_target="$VERILOG_GENERATOR_REPORT_ROOT/.validation-home"
mkdir -p "$validation_home_target"
cp -R "$validation_home_source/." "$validation_home_target/"

{str_py} --version
{dict_context['str_vivado_snippet']}
{dict_context['str_simulator_priority_snippet']}
for tool in xvlog xelab xsim vcs verdi iverilog vvp yosys; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "$tool=present"
  else
    echo "$tool=missing"
  fi
done
if command -v xvlog >/dev/null 2>&1 && command -v xelab >/dev/null 2>&1 && command -v xsim >/dev/null 2>&1; then
  expected_sim_backend=xsim
elif command -v vcs >/dev/null 2>&1 && command -v verdi >/dev/null 2>&1; then
  expected_sim_backend=vcs_verdi
elif command -v iverilog >/dev/null 2>&1 && command -v vvp >/dev/null 2>&1; then
  expected_sim_backend=iverilog
else
  echo "No supported simulator backend is available on the remote server." >&2
  exit 1
fi
if command -v yosys >/dev/null 2>&1; then
  yosys_available=1
else
  yosys_available=0
fi
{str_py} -m compileall -q \
  skills/readable-verilog-generator/scripts \
  tests

{dict_context['str_phase_runner']}

# targeted 阶段先验证本次证据链直接覆盖的入口，缩短局部回归反馈路径。
run_pytest_phase targeted {sh_quote(dict_context['str_targeted_pytest_command'])}

# regression 阶段复用同一命令摘要与退出码契约，覆盖质量、验证、工作流和远程行为族，阻断跨模块证据漂移。
run_pytest_phase regression {sh_quote(dict_context['str_regression_pytest_command'])}

# full 阶段覆盖完整测试树，形成与发行候选绑定的最终计数基线。
run_pytest_phase full {sh_quote(dict_context['str_full_pytest_command'])}

# 将 full 阶段原始控制台字节复制到旧聚合路径，保证旧消费者读取同一最终退出阶段。
cp "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_pytest_full.log" \
  "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_pytest.log"

# 将 full 阶段 JSON 摘要复制到旧聚合路径，使旧解析器继续读取同一 phase、exit_code 和 summary_line 证据。
cp "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_pytest_full_summary.json" \
  "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_pytest_summary.json"

# 三阶段 pytest 已完成，先写入 post-pytest 起始标记，再进入 smoke 入口。
is_post_pytest_phase_enabled=1
name_post_pytest_phase="smoke"
printf '{{"phase":"post_pytest","status":"started","exit_code":0,"timestamp":"%s"}}\n' \\
  "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)" > "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_post_pytest_phase.json"

# workflow CLI 的生成目录必须留在 staged skill workspace，完成后再复制到 outer reports 归档。
workflow_workspace_root="$PWD/.smoke-scratch"
workflow_execute_root="$workflow_workspace_root/remote_execute"
workflow_implement_root="$workflow_workspace_root/{dict_context['str_implement_archive_root']}"
export VERILOG_GENERATOR_IMPLEMENT_ARCHIVE_ROOT={sh_quote(dict_context['str_implement_archive_root'])}
str_implement_validation_root="$VERILOG_GENERATOR_SMOKE_RUN_DIR"
VERILOG_GENERATOR_IMPLEMENT_VALIDATION="$str_implement_validation_root/{dict_context['str_implement_validation']}"
export VERILOG_GENERATOR_IMPLEMENT_VALIDATION
mkdir -p "$workflow_workspace_root"

# smoke runner 继续执行 fixture、工具链和 readiness 约束，并保留真实退出码。
set +e
{str_py} -m tests.smoke.run_smoke \
  --settings skills/readable-verilog-generator/config/defaults.json \
  --run-dir "$VERILOG_GENERATOR_SMOKE_RUN_DIR" 2>&1 | tee "$path_post_pytest_log"
exit_code_post_pytest="${{PIPESTATUS[0]}}"
set -e
if [ "$exit_code_post_pytest" -eq 0 ]; then
  status_post_pytest="passed"
else
  status_post_pytest="failed"
fi
printf '{{"phase":"%s","status":"%s","exit_code":%s,"timestamp":"%s"}}\n' \\
  "$name_post_pytest_phase" "$status_post_pytest" "$exit_code_post_pytest" "$(date -u +%Y-%m-%dT%H:%M:%S.%3NZ)" \\
  > "$path_post_pytest_phase"
if [ "$exit_code_post_pytest" -ne 0 ]; then
  exit "$exit_code_post_pytest"
fi

# smoke 已通过，后续工作流失败时由退出钩子记录当前阶段。
name_post_pytest_phase="workflow"
{dict_context['str_rtl_md_snippet']}
{dict_context['str_filename_gate_snippet']}
if [ -n "$configured_simulator_backend" ]; then
  export VERILOG_GENERATOR_SIMULATOR_PRIORITY="$configured_simulator_backend"
  expected_sim_backend="$configured_simulator_backend"
fi
{str_py} -m {dict_context['str_workflow_module_quoted']} run-workflow \
  --spec {dict_context['str_workflow_spec']} \
  --out-dir "$workflow_execute_root" \
  --model-provider mock \
  --readiness execute \
  --external-target local
{str_py} -m {dict_context['str_workflow_module_quoted']} validate \
  --spec {dict_context['str_workflow_spec']} \
  --path "$workflow_execute_root/{dict_context['str_execute_attempt']}/rtl/generated" \
  --readiness execute \
  --external-target local
mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR/{dict_context['str_execute_archive_root']}"
cp -R "$workflow_execute_root/." "$VERILOG_GENERATOR_SMOKE_RUN_DIR/{dict_context['str_execute_archive_root']}/"

# workflow 证据已归档，后续失败归因切换到 fixture 阶段。
name_post_pytest_phase="fixture"
EXPECTED_SIM_BACKEND="$expected_sim_backend" \
REMOTE_EXECUTE_VALIDATION="$VERILOG_GENERATOR_SMOKE_RUN_DIR/{dict_context['str_execute_validation']}" \
{str_py} - <<'PY'
import base64
import json
import os
from pathlib import Path
expected = os.environ["EXPECTED_SIM_BACKEND"]
smoke_root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"])
validation = json.loads(Path(os.environ["REMOTE_EXECUTE_VALIDATION"]).read_text(encoding="utf-8"))
metrics = validation["metrics"]
assert metrics["selected_simulator_backend"] == expected, metrics
assert set(["xvlog", "xelab", "xsim"]).issubset(metrics["executed_tools"]) if expected == "xsim" else True, metrics
if expected == "iverilog":
    assert "xsim" in metrics["missing_preferred_backends"], metrics
    assert "vcs_verdi" in metrics["missing_preferred_backends"], metrics
PY
mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR/{dict_context['str_fixture_archive_root']}"
REMOTE_FIXTURES="{dict_context['str_fixture_names']}" EXPECTED_SIM_BACKEND="$expected_sim_backend" \
REMOTE_FIXTURE_ASSET_ROOT={dict_context['str_fixture_asset_root']} \
REMOTE_FIXTURE_SCRATCH_ROOT={dict_context['str_fixture_scratch_root']} \
REMOTE_FIXTURE_ARCHIVE_ROOT={dict_context['str_fixture_archive_root']} \
REMOTE_QUALITY_GATE_MODULE={dict_context['str_quality_gate_module']} \
REMOTE_PRECHECK_MANIFEST_B64={dict_context['str_precheck_manifest_b64']} \
REMOTE_TESTBENCH_PREFIX={dict_context['str_testbench_prefix']} \
REMOTE_TESTBENCH_SUFFIX={dict_context['str_testbench_suffix']} \
{str_py} - <<'PY'
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# authority 生成的默认模块声明保留在内联作用域，环境变量只覆盖本轮配置值。
WORKFLOW_CLI_MODULE = "{dict_context['str_workflow_module']}"
WORKFLOW_CLI_MODULE = os.environ.get("VERILOG_GENERATOR_WORKFLOW_MODULE") or WORKFLOW_CLI_MODULE

fixtures = os.environ["REMOTE_FIXTURES"].split()
expected = os.environ["EXPECTED_SIM_BACKEND"]
smoke_root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"])
fixture_workspace_root = Path(os.environ["REMOTE_FIXTURE_SCRATCH_ROOT"])
fixture_archive_root = smoke_root / os.environ["REMOTE_FIXTURE_ARCHIVE_ROOT"]
fixture_workspace_root.mkdir(parents=True, exist_ok=True)
fixture_archive_root.mkdir(parents=True, exist_ok=True)
summary = {{"fixtures": []}}
fixture_asset_root = Path(os.environ["REMOTE_FIXTURE_ASSET_ROOT"])
quality_gate_module = os.environ["REMOTE_QUALITY_GATE_MODULE"]
prechecks = json.loads(base64.b64decode(os.environ["REMOTE_PRECHECK_MANIFEST_B64"]).decode("utf-8"))
bad_check = prechecks["bad_quality"]
bad_case_name = bad_check["case"]
bad_source = fixture_asset_root / bad_case_name / bad_check["source"]
bad_report = fixture_workspace_root / bad_case_name / "bad_quality_gate.json"
bad_markdown = fixture_workspace_root / bad_case_name / "bad_quality_gate.md"
bad_report.parent.mkdir(parents=True, exist_ok=True)
bad_command = [
    sys.executable,
    "-m",
    quality_gate_module,
    str(bad_source),
    "--json",
    str(bad_report),
    "--markdown",
    str(bad_markdown),
]
bad_result = subprocess.run(bad_command, check=False)
assert bad_result.returncode != 0, bad_result.returncode
bad_payload = json.loads(bad_report.read_text(encoding="utf-8"))
bad_gate_result = next(
    item for item in bad_payload["vg_rule_results"] if item["gate_id"] == bad_check["rule"]
)
assert bad_payload["ok"] is False, bad_payload
assert bad_gate_result["status"] == "failed", bad_gate_result
hierarchy_checks = prechecks.get("hierarchy", [])
hierarchy_root = fixture_asset_root / prechecks.get("hierarchy_case", "")
for hierarchy_check in hierarchy_checks:
    source_name = hierarchy_check["source"]
    gate_id = hierarchy_check["rule"]
    expected_status = hierarchy_check["status"]
    expected_count = hierarchy_check.get("operation_count")
    expected_path = hierarchy_check.get("path")
    source_path = hierarchy_root / source_name
    hierarchy_case_name = hierarchy_root.name
    probe_report = fixture_workspace_root / hierarchy_case_name / (
        source_path.stem + "_quality_gate.json"
    )
    probe_markdown = probe_report.with_suffix(".md")
    probe_report.parent.mkdir(parents=True, exist_ok=True)
    probe_result = subprocess.run(
        [
            sys.executable,
            "-m",
            quality_gate_module,
            str(source_path),
            "--json",
            str(probe_report),
            "--markdown",
            str(probe_markdown),
        ],
        check=False,
    )
    probe_payload = json.loads(probe_report.read_text(encoding="utf-8"))
    gate_result = next(
        item for item in probe_payload["vg_rule_results"] if item["gate_id"] == gate_id
    )
    assert gate_result["status"] == expected_status, gate_result
    if expected_status == "failed":
        assert probe_result.returncode != 0, probe_result.returncode
    if expected_count is not None:
        def evidence_text(finding):
            evidence_value = finding.get("evidence")
            if isinstance(evidence_value, dict):
                return str(
                    evidence_value.get("detail")
                    or evidence_value.get("source_excerpt")
                    or ""
                )
            return str(evidence_value or "")

        evidence = "\\n".join(
            evidence_text(item) for item in gate_result["findings"]
        )
        assert f"operation_count={{expected_count}}" in evidence, evidence
        assert expected_path in evidence, evidence
for name in fixtures:
    source_root = fixture_asset_root / name
    staged_root = fixture_workspace_root / name
    generated = staged_root / "generated"
    shutil.copytree(source_root / "generated", generated, dirs_exist_ok=True)
    testbench_name = os.environ["REMOTE_TESTBENCH_PREFIX"] + name + os.environ["REMOTE_TESTBENCH_SUFFIX"]
    staged_testbench = generated / "tb" / testbench_name
    spec_payload = json.loads((source_root / "spec.json").read_text(encoding="utf-8"))
    for output in spec_payload.get("outputs", []):
        if output.get("kind") == "testbench":
            output["path"] = "tb/" + staged_testbench.name
    spec = staged_root / "spec.json"
    spec.write_text(json.dumps(spec_payload, indent=2, sort_keys=True), encoding="utf-8")
    report_json = staged_root / "validation.json"
    report_json.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        WORKFLOW_CLI_MODULE,
        "validate",
        "--spec",
        str(spec),
        "--path",
        str(generated),
        "--readiness",
        "execute",
        "--external-target",
        "local",
        "--report-json",
        str(report_json),
    ]
    subprocess.run(command, check=True)
    report = json.loads(report_json.read_text(encoding="utf-8"))
    metrics = report["metrics"]
    assert report["ok"] is True, report
    assert metrics["selected_simulator_backend"] == expected, metrics
    if expected == "xsim":
        assert set(["xvlog", "xelab", "xsim"]).issubset(metrics["executed_tools"]), metrics
    archive_root = fixture_archive_root / name
    archive_generated = archive_root / "generated"
    archive_report_json = archive_root / "validation.json"
    outputs = report.get("spec_outputs", [])
    summary["fixtures"].append({{
        "name": name,
        "ok": report["ok"],
        "selected_simulator_backend": metrics["selected_simulator_backend"],
        "executed_tools": metrics["executed_tools"],
        "rtl_path": str(archive_generated / "rtl" / (name + ".v")),
        "testbench_path": str(archive_generated / "tb" / ("tb_" + name + ".v")),
        "validation_json": str(archive_report_json),
        "outputs": outputs,
    }})
shutil.copytree(fixture_workspace_root, fixture_archive_root, dirs_exist_ok=True)
(fixture_archive_root / "summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True),
    encoding="utf-8",
)
PY
if [ "$yosys_available" -eq 1 ]; then
  name_post_pytest_phase="implement"
{str_py} -m {dict_context['str_workflow_module_quoted']} run-workflow \
    --spec {dict_context['str_workflow_spec']} \
    --out-dir "$workflow_implement_root" \
    --model-provider mock \
    --readiness implement \
    --external-target local
  mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR/{dict_context['str_implement_archive_root']}"
  cp -R "$workflow_implement_root/." "$VERILOG_GENERATOR_SMOKE_RUN_DIR/{dict_context['str_implement_archive_root']}/"
  {str_py} - <<'PY'
import json
import os
from pathlib import Path
smoke_root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"])
path_implement_result = smoke_root / os.environ["VERILOG_GENERATOR_IMPLEMENT_ARCHIVE_ROOT"] / "workflow_result.json"
result = json.loads(path_implement_result.read_text(encoding="utf-8"))
assert result["status"] == "passed", result
PY
else
  set +e
  {str_py} -m {dict_context['str_workflow_module_quoted']} run-workflow \
    --spec {dict_context['str_workflow_spec']} \
    --out-dir "$workflow_implement_root" \
    --model-provider mock \
    --readiness implement \
    --external-target local
  impl_status=$?
  set -e
  mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR/{dict_context['str_implement_archive_root']}"
  cp -R "$workflow_implement_root/." "$VERILOG_GENERATOR_SMOKE_RUN_DIR/{dict_context['str_implement_archive_root']}/"
  if [ "$impl_status" -eq 0 ]; then
    echo "Expected implement readiness to block when yosys is missing." >&2
    exit 1
  fi
  {str_py} - <<'PY'
import json
import os
from pathlib import Path
smoke_root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"])
path_implement_result = smoke_root / os.environ["VERILOG_GENERATOR_IMPLEMENT_ARCHIVE_ROOT"] / "workflow_result.json"
result = json.loads(path_implement_result.read_text(encoding="utf-8"))
assert result["status"] == "blocked_toolchain", result
validation = json.loads(Path(os.environ["VERILOG_GENERATOR_IMPLEMENT_VALIDATION"]).read_text(encoding="utf-8"))
assert any(
    item.get("tool") == "yosys" and item.get("source") == "toolchain_issue"
    for item in validation["issues"]
), validation
PY
fi

# runtime 模块汇总环境、cwd、压力、归档和三阶段测试事实。
name_post_pytest_phase="evidence"
{str_py} -m scripts.python.remote.remote_evidence_runtime
""".strip()

    # 主命令摘要绑定本次实际执行合同，避免完成清单只证明了文件存在。
    str_command_digest = hashlib.sha256(str_command_body.encode("utf-8")).hexdigest()  # 远程主命令体 SHA-256

    # 最终写入器只在主命令全部成功后执行，并通过临时文件替换保证原子可见。
    str_completion_snippet = f"""
VERILOG_GENERATOR_RUN_ID={sh_quote(dict_context['str_run_id'])} \\
VERILOG_GENERATOR_SOURCE_DIGEST={dict_context['str_source_digest']} \\
VERILOG_GENERATOR_COMMAND_DIGEST={str_command_digest} \\
{str_py} - <<'PY'
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

smoke_root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"])
evidence_path = smoke_root / "remote_test_evidence.json"
evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
evidence_sha256 = hashlib.sha256(
    json.dumps(evidence_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
completion_path = smoke_root / "completion.json"
temporary_path = smoke_root / "completion.json.tmp"
payload = {{
    "status": "passed",
    "run_id": os.environ["VERILOG_GENERATOR_RUN_ID"],
    "source_digest": os.environ["VERILOG_GENERATOR_SOURCE_DIGEST"],
    "command_digest": os.environ["VERILOG_GENERATOR_COMMAND_DIGEST"],
    "evidence_sha256": evidence_sha256,
    "started_at": os.environ["VERILOG_GENERATOR_STARTED_AT"],
    "completed_at": datetime.now(timezone.utc).isoformat(),
}}
temporary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
temporary_path.replace(completion_path)
PY
""".strip()

    # 完成清单写入后再执行可选清理；默认 retained 模式会保留全部证据。
    return "\n".join(
        (
            str_command_body,
            str_completion_snippet,
            dict_context["str_cleanup_snippet"],
            dict_context["str_bytecode_cleanup"],
        )
    )

# remote_validation_transport_command 把长 bash 正文压缩为 Windows 可传输的短命令。
def remote_validation_transport_command(
    str_command: str,
    str_remote_python: str,
    *,
    str_report_root: str,
    str_agent_review_path: str,
) -> str:
    """生成解压并执行远端验证正文的短 shell 命令。

    :param str_command: 已完成路径和工具链绑定的远端 bash 正文。
    :param str_remote_python: 远端可用的 Python 命令文本。
    :param str_report_root: outer run 直接报告目录的相对路径。
    :param str_agent_review_path: Agent 审核文件的 outer run 相对路径。
    :return: 可交给 request-command 的压缩传输命令。
    """

    # UTF-8 字节是跨 Windows 与 Linux 的唯一压缩输入，避免本地代码页改变摘要。
    bytes_command = str_command.encode("utf-8")  # 远端 bash 正文的原始 UTF-8 字节

    # 固定 gzip 时间元数据，使相同正文得到可复核的压缩结果。
    bytes_compressed = gzip.compress(bytes_command, compresslevel=9, mtime=0)  # 远程传输用的压缩正文

    # base64 把压缩字节限制在安全的 shell 单引号字符集内。
    str_encoded_command = base64.b64encode(bytes_compressed).decode("ascii")  # 无换行的 shell 传输载荷

    # 原文摘要写入 request 注释，便于 Agent 对传输正文做完整性核对。
    str_command_digest = hashlib.sha256(bytes_command).hexdigest()  # 远端解码前的正文 SHA-256

    # 远端 Python 只把解压后的 bash 字节写入管道，阶段逻辑仍由原始正文执行。
    str_python_code = (
        "import base64,gzip,sys;"
        f"sys.stdout.buffer.write(gzip.decompress(base64.b64decode('{str_encoded_command}')))"
    )  # 远端 Python 解码表达式

    # 明文保留两条报告路径，使 request 审计仍能直接确认固定 runs 布局。
    list_transport_lines = [  # 传输 wrapper 的可读审核行序列
        f"# 报告目录固定为 outer run 的直接子目录：{str_report_root}",  # 直接报告路径审核行
        f"# Agent 审核文件固定在 outer run 根：{str_agent_review_path}",  # Agent 审核路径审核行
        f"# 压缩正文摘要用于传输完整性核对：{str_command_digest}",  # 完整性摘要审核行
        f"{sh_quote(str_remote_python)} -c {sh_quote(str_python_code)} | bash",  # 远端解压执行行
    ]

    # 换行只分隔短注释与执行命令，不改变解压后 bash 正文的字节内容。
    return "\n".join(list_transport_lines)

# filename_gate_remote_snippet 生成 authority-selected VG148/VG149 交付门片段。
def filename_gate_remote_snippet(
    str_remote_python: str,
    validation_authority: Mapping[str, Any] | None = None,
) -> str:
    """生成远端文件名门禁与合法 testbench 仿真片段。

    :param str_remote_python: 远端 Python 命令。
    :param validation_authority: 可选 remote.validation authority。
    :return: 可嵌入主 bash 脚本的文件名回归片段。
    """

    # 文件名门禁正文已拆到独立模块，保持旧入口和输出合同不变。
    return build_filename_gate_remote_snippet(
        str_remote_python,
        validation_authority=validation_authority,
    )

# rtl_md_constraint_remote_snippet 生成 RTL Markdown 约束远端回归片段。
def rtl_md_constraint_remote_snippet(str_remote_python: str) -> str:
    """生成远端 RTL Markdown 约束回归脚本片段。

    :param str_remote_python: 远端 Python 命令。
    :return: 可嵌入主 bash 脚本的约束回归片段。
    """

    # Python 命令进入 shell 前必须转义。
    str_py = sh_quote(str_remote_python)  # RTL Markdown 片段使用的 Python 命令

    # 模板中的 __PY__ 占位符稍后替换为转义后的 Python 命令。
    str_template = r"""
mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_verilog_quality_gates"
__PY__ - <<'PY'
import os
import shutil
from pathlib import Path

from scripts.python.workflow.prompt import render_prompt
from scripts.python.workflow.verilog_gate_catalog import load_verilog_quality_gates, summarize_constraints_for_prompt
from scripts.python.quality.vg_semantic_engine import run_vg_semantic_gate


def spec(name="remote_verilog_quality_gates"):
    return {
        "name": name,
        "description": "Remote RTL Markdown constraint regression fixture.",
        "behavior": ["Register one input bit."],
        "constraints": [],
        "notes": [],
        "clock": {"name": "clk", "edge": "posedge"},
        "reset": {"name": "rst_n", "active": "low", "synchronous": False},
        "interfaces": {
            "ports": [
                {"name": "clk", "direction": "input", "width": 1, "role": "clock"},
                {"name": "rst_n", "direction": "input", "width": 1, "role": "reset"},
                {"name": "a", "direction": "input", "width": 4},
                {"name": "y", "direction": "output", "width": 1},
            ]
        },
        "outputs": [{"path": f"rtl/{name}.v", "kind": "source", "language": "verilog"}],
    }


catalog = load_verilog_quality_gates()
# catalog 是规则数量的唯一事实源；远程片段不重复维护固定总数。
active_rule_count = int(catalog["active_rules"])
assert int(catalog["total_rules"]) == active_rule_count, catalog
assert active_rule_count > 0, catalog
assert int(catalog["reserved_rules"]) == 0, catalog
prompt = render_prompt(spec(), stage="rtl")
for marker in (
    "Verilog quality gates",
    "VG072",
    "VG111",
    "VG145",
    "VG147",
    "VG148",
    "VG149",
    "VG150",
):
    assert marker in prompt, marker
summary = summarize_constraints_for_prompt(max_rules_per_group=3)
assert f"{active_rule_count} active gates" in summary, summary
assert "reserved gates" not in summary, summary

smoke_root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"])
quality_workspace_root = Path(".smoke-scratch/remote_verilog_quality_gates")
quality_archive_root = smoke_root / "remote_verilog_quality_gates"
quality_workspace_root.mkdir(parents=True, exist_ok=True)
quality_archive_root.mkdir(parents=True, exist_ok=True)
bad_dir = quality_workspace_root / "bad"
bad_dir.mkdir(parents=True, exist_ok=True)
(bad_dir / "bad_constraints.v").write_text(
    "\n".join(
        [
            "module bad_constraints (",
            "    input wire clk,",
            "    input wire rst_n,",
            "    input wire [3:0] a,",
            "    output reg y",
            ");",
            "wire gated_clk = clk & rst_n;",
            "initial y = 1'b0;",
            "always @(a || rst_n) begin",
            "  if (a == 4'bx) begin",
            "    y <= 1'b1;",
            "  end",
            "  case (4'b0000)",
            "    4'b0001: y = 1'b1;",
            "  endcase",
            "  for (i = start; i < LIMIT; i = i + 1) begin",
            "    y = y;",
            "  end",
            "end",
            "always @(*) begin",
            "  if (a[0]) begin",
            "    y = 1'b0;",
            "  end",
            "end",
            "endmodule",
            "",
        ]
    ),
    encoding="utf-8",
)
bad_report = run_vg_semantic_gate(bad_dir, spec=spec("bad_constraints"))
codes = {
    result["gate_id"]
    for result in bad_report["vg_rule_results"]
    if result["status"] == "failed"
}
for expected in (
    "VG078",
    "VG101",
    "VG111",
    "VG117",
    "VG124",
    "VG079",
    "VG090",
    "VG142",
    "VG109",
):
    assert expected in codes, codes

good_dir = quality_workspace_root / "good"
good_dir.mkdir(parents=True, exist_ok=True)
(good_dir / "good_constraints.v").write_text(
    "\n".join(
        [
            "module good_constraints (",
            "    input wire clk,",
            "    input wire rst_n,",
            "    input wire [3:0] a,",
            "    output reg y",
            ");",
            "always @(posedge clk or negedge rst_n) begin",
            "  if (!rst_n) begin",
            "    y <= 1'b0;",
            "  end else begin",
            "    y <= a[0];",
            "  end",
            "end",
            "endmodule",
            "",
        ]
    ),
    encoding="utf-8",
)
good_report = run_vg_semantic_gate(good_dir, spec=spec("good_constraints"))
assert good_report["delivery_ready"] is True, good_report["delivery_issues_by_rule"]
shutil.copytree(quality_workspace_root, quality_archive_root, dirs_exist_ok=True)
PY
eval_skill_workspace_output="$PWD/.smoke-scratch/remote_eval_skill.json"
mkdir -p "$(dirname "$eval_skill_workspace_output")"
__PY__ -m scripts.python.workflow.cli eval-skill \
  --evals skills/readable-verilog-generator/evals/evals.json \
  --out "$eval_skill_workspace_output" \
  --no-state
cp "$eval_skill_workspace_output" "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_eval_skill.json"
__PY__ - <<'PY'
import json
import os
from pathlib import Path

smoke_root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"])
report = json.loads((smoke_root / "remote_eval_skill.json").read_text(encoding="utf-8"))
summary = report["summary"]
assert summary["ok"] is True, summary
assert summary["case_count"] >= 30, summary
case = next((item for item in report["cases"] if item.get("id") == "vg_semantic_gate_regression"), None)
assert case and case.get("passed") is True, case
PY
""".strip()

    # 替换占位符后返回可嵌入主脚本的片段。
    return str_template.replace("__PY__", str_py)

# remote_output_cleanup_snippet 生成远端输出保留或清理片段。
def remote_output_cleanup_snippet(cleanup_outputs: bool, str_remote_python: str = "python3") -> str:
    """生成远端 smoke 输出处理脚本片段。

    :param cleanup_outputs: 是否删除远端 smoke 输出。
    :param str_remote_python: 远端 Python 命令。
    :return: 可嵌入主 bash 脚本的输出处理片段。
    """

    # 远端 Python 命令先完成 quoting，再委托支撑模块生成策略正文。
    str_remote_python_quoted = sh_quote(str_remote_python)  # 输出处置片段使用的安全 Python 命令

    # 保持旧公开入口兼容。
    return build_remote_output_cleanup_snippet(cleanup_outputs, str_remote_python_quoted)

# remote_bytecode_cleanup_snippet 保留远端 bytecode 清理策略说明。
def remote_bytecode_cleanup_snippet(str_remote_python: str) -> str:
    """生成远端 bytecode 清理策略片段。

    :param str_remote_python: 远端 Python 命令；当前仅用于保持函数签名兼容。
    :return: 表示 retained workspace 不删除 bytecode 的 echo 片段。
    """

    # retained 验证工作区需要完整证据，不主动删除远端缓存。
    return "echo 'remote_bytecode_cleanup_skipped=retained_validation_workspace'"

# simulator_priority_export_snippet 生成仿真后端优先级片段。
def simulator_priority_export_snippet(str_selected_backend: str) -> str:
    """生成远端仿真后端选择 shell 片段。

    :param str_selected_backend: 用户确认的仿真后端；空字符串表示自动选择。
    :return: 设置 configured_simulator_backend 的 shell 片段。
    """

    # 空后端表示按远端工具可用性自动选择。
    if not str_selected_backend:

        # 自动模式不导出 VERILOG_GENERATOR_SIMULATOR_PRIORITY。
        return "configured_simulator_backend=''\necho 'simulator_backend_selection=auto_priority'"

    # 已确认后端要通过环境变量覆盖 runtime 默认优先级。
    return (
        f"configured_simulator_backend={sh_quote(str_selected_backend)}\n"
        "printf '%s\\n' \"simulator_backend_selection=$configured_simulator_backend\""
    )

# vivado_activation_snippet 生成 xsim 所需 Vivado settings64.sh 激活片段。
def vivado_activation_snippet(
    str_selected_vivado: str = "",
    str_selected_backend: str = "",
    remote_runtime_config_path: str | None = None,
    validation_authority: Mapping[str, Any] | None = None,
) -> str:
    """生成远端 Vivado settings64.sh 发现和激活脚本。

    :param str_selected_vivado: 用户确认的 settings64.sh 绝对路径。
    :param str_selected_backend: 用户确认的仿真后端。
    :param remote_runtime_config_path: 用于提示用户持久化选择的配置路径。
    :param validation_authority: 可选的远程 validation authority。
    :return: 可嵌入主 bash 脚本的 Vivado 激活片段。
    """

    # 多版本配置提示路径。
    str_config_hint = str(  # 阻断提示路径
        remote_runtime_config_path or remote_runtime_settings_relpath()  # 多版本 Vivado 时用户需更新的 runtime 配置路径
    )

    # 非 xsim 跳过激活。
    if str_selected_backend and str_selected_backend != "xsim":

        # 仍输出短状态，便于远端日志说明为何跳过 Vivado。
        return "echo 'vivado_settings=not_required_for_selected_backend'"

    # 多候选阻断。
    dict_vivado_authority = validation_authority or dict_remote_validation_authority  # 选择本次远程调用的 Vivado authority

    # 读取 authority 声明的 settings64.sh glob 列表。
    list_vivado_globs = dict_vivado_authority["vivado_settings_globs"]  # authority Vivado 候选模式

    # 将 authority 模式拼成 shell for 循环的续行文本，避免末项吞掉分号。
    str_vivado_globs = " \\\n".join("  {}".format(str_glob) for str_glob in list_vivado_globs)  # shell 候选模式文本

    # 返回由 authority 候选模式展开的远端激活脚本。
    return f"""
selected_vivado_settings={sh_quote(str_selected_vivado)}
selected_vivado_settings="${{selected_vivado_settings:-${{VERILOG_GENERATOR_VIVADO_SETTINGS64:-}}}}"
export VERILOG_GENERATOR_VIVADO_SETTINGS64="$selected_vivado_settings"
toolchain_config_hint={sh_quote(str_config_hint)}
vivado_candidates_file="$(mktemp)"
for candidate in \
  "${{XILINX_VIVADO:-}}/settings64.sh" \
  "${{XILINX_VIVADO:-}}/../settings64.sh" \
{str_vivado_globs}; do
  if [ -f "$candidate" ]; then
    readlink -f "$candidate"
  fi
done | sort -u > "$vivado_candidates_file"
vivado_candidate_count="$(wc -l < "$vivado_candidates_file" | tr -d ' ')"
if [ -n "$selected_vivado_settings" ]; then
  if ! grep -Fx "$selected_vivado_settings" "$vivado_candidates_file" >/dev/null 2>&1; then
    echo "Configured Xilinx settings64.sh was not found on the remote server: $selected_vivado_settings" >&2
    echo "Available Xilinx toolchain choices:" >&2
    cat "$vivado_candidates_file" >&2
    exit 2
  fi
  echo "vivado_settings=$selected_vivado_settings"
  # shellcheck disable=SC1090
  . "$selected_vivado_settings"
elif [ "$vivado_candidate_count" -gt 1 ]; then
  echo "TOOLCHAIN_SELECTION_REQUIRED=1" >&2
  echo "Multiple Xilinx toolchain settings64.sh candidates were detected. Ask the user to choose one" >&2
  echo "and persist it in: $toolchain_config_hint" >&2
  echo "Available Xilinx toolchain choices:" >&2
  cat "$vivado_candidates_file" >&2
  exit 2
elif ! command -v xvlog >/dev/null 2>&1 || ! command -v xelab >/dev/null 2>&1 || ! command -v xsim >/dev/null 2>&1; then
  if [ "$vivado_candidate_count" -eq 1 ]; then
    auto_vivado_settings="$(cat "$vivado_candidates_file")"
    echo "vivado_settings=$auto_vivado_settings"
    # shellcheck disable=SC1090
    . "$auto_vivado_settings"
  fi
fi
""".strip()

# _require_remote_relative_path 校验远端 workdir 内相对路径。
def _require_remote_relative_path(str_value: str) -> str:
    """校验远端相对 POSIX 路径。

    :param str_value: 待校验路径文本。
    :return: 归一化后的 POSIX 相对路径。
    :raises ValueError: 路径为空、绝对、含反斜杠或含父目录时抛出。
    """

    # 去除首尾空白后检查远端相对路径空值。
    str_raw = str_value.strip()  # 待校验的远端相对路径文本

    # 空路径会导致远端 helper 目标不明确。
    if not str_raw:

        # 空相对路径无法映射到远端 workdir 内的稳定目标。
        raise ValueError("> ERR: [Python] remote relative path must not be empty.")

    # 远端路径必须使用 POSIX 分隔符。
    if "\\" in str_raw:

        # 反斜杠可能表示本地 Windows 路径，远端 helper 不能安全解释。
        raise ValueError("> ERR: [Python] remote relative path must use POSIX separators.")

    # PurePosixPath 负责归一化 POSIX 片段。
    path_remote = PurePosixPath(str_raw)  # POSIX 路径对象

    # retained run 和配置路径必须相对远端 workdir。
    if path_remote.is_absolute() or str_raw.startswith("~"):

        # retained run 只能落在配置的远端 workdir 内部。
        raise ValueError(
            "> ERR: [Python] remote path must be relative to the configured remote workdir."
        )

    # 路径片段决定 retained run 是否会逃出配置的远端工作区。
    tuple_parts = path_remote.parts  # 防止相对路径含当前目录或父目录穿越的片段序列

    # 空片段、当前目录和父目录都不允许。
    if not tuple_parts or any(str_part in {"", ".", ".."} for str_part in tuple_parts):

        # 父目录穿越会突破远端 workdir 约束。
        raise ValueError(
            "> ERR: [Python] remote relative path must be normalized without parent traversal."
        )

    # 返回 POSIX 字符串供 shell 和 helper 使用。
    return path_remote.as_posix()

# remote_join 拼接远端 workdir 内相对路径。
def remote_join(*tuple_parts: str) -> str:
    """拼接多个远端相对 POSIX 路径片段。

    :param tuple_parts: 一个或多个远端相对路径片段。
    :return: 归一化后的 POSIX 相对路径。
    """

    # 收集校验后的路径片段。
    list_normalized: list[str] = []  # 拼接前的 POSIX 路径片段

    # 逐个校验片段，禁止任何片段含父目录或绝对路径。
    for int_index, str_part in enumerate(tuple_parts):

        # 单个片段校验后再拆成 PurePosixPath parts。
        str_value = _require_remote_relative_path(str_part)  # 已归一化的远端路径片段

        # PurePosixPath.parts 保留多级片段。
        list_normalized.extend(PurePosixPath(str_value).parts)

    # 拼回 POSIX 相对路径。
    return PurePosixPath(*list_normalized).as_posix()

# sh_quote 对 shell 单引号参数做安全转义。
def sh_quote(str_value: str) -> str:
    """用单引号转义 shell 参数。

    :param str_value: 待嵌入 bash 脚本的原始文本。
    :return: 可放入 shell 命令行的单引号字符串。
    """

    # 单引号内部的单引号按 POSIX shell 习惯拆开转义。
    return "'" + str_value.replace("'", "'\"'\"'") + "'"
