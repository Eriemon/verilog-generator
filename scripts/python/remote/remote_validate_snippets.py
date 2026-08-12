"""远端验证 shell 片段、常量和 POSIX 路径辅助函数。"""

# 标准库负责命令摘要和远程传输正文的可逆压缩。
import base64
import gzip
import hashlib
import sys

# pathlib 负责远端 retained run 常量中的 POSIX 路径。
from pathlib import Path, PurePosixPath

# Any 只用于工具链选择字典这类异构载荷。
from typing import Any

# skill 主体根目录供脚本直运行时定位 runtime 包。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # 包含 runtime、scripts 与 config 的 skill 根目录

# workflow CLI 统一切到 scripts/python/workflow 官方模块入口。
WORKFLOW_CLI_MODULE = "scripts.python.workflow.cli"  # workflow 官方 CLI 模块名

# 需要进入 simulator 的固定 fixture 不包含故意违规的文件名案例。
REMOTE_SIMULATION_FIXTURES = (  # 五类合法 RTL 仿真回归用例
    "comb_operation_budget",  # 覆盖 VG146 负例与注册流水修复后的时序行为
    "comb_hierarchy_budget",  # 覆盖跨实例 source closure、Q 切点与 loop 归属
    "comb_parity_mux",  # 覆盖组合奇偶校验与 mux 输出选择链路
    "pipeline_delay",  # 覆盖多拍寄存器延迟和复位后的数据推进
    "ready_valid_slice",  # 覆盖 ready-valid 反压握手与数据保持约束
)

# 远端总 fixture 清单额外登记只进入交付门禁的文件名案例。
REMOTE_FIXTURES = (  # retained run 汇总使用的六类回归身份
    *REMOTE_SIMULATION_FIXTURES,  # 合法 RTL 仿真 fixture
    "file_naming_gates",  # VG148/VG149 文件名与角色确认 probe
)

# remote_execute attempt-001 是远端主流程的稳定证据根。
REMOTE_EXECUTE_ROOT = PurePosixPath("remote_execute") / "attempt-001"  # smoke 运行目录内的主流程证据相对根

# remote_fixtures 保存固定小用例聚合报告。
REMOTE_FIXTURE_ROOT = PurePosixPath("remote_fixtures")  # smoke 运行目录内的 fixture 证据相对根

# remote_pytest_summary.json 保存权威远程 pytest 的精确计数和耗时。
REMOTE_PYTEST_SUMMARY_JSON = PurePosixPath("remote_pytest_summary.json")  # smoke 运行目录内的 pytest 结构化证据路径

# targeted 阶段摘要保留定向回归的真实命令和计数。
REMOTE_PYTEST_TARGETED_SUMMARY_JSON = PurePosixPath("remote_pytest_targeted_summary.json")  # targeted 阶段摘要路径

# regression 阶段摘要保留行为族回归的真实命令和计数。
REMOTE_PYTEST_REGRESSION_SUMMARY_JSON = PurePosixPath("remote_pytest_regression_summary.json")  # 行为族回归摘要的 retained 路径

# full 阶段摘要保留完整测试树的真实命令和计数。
REMOTE_PYTEST_FULL_SUMMARY_JSON = PurePosixPath("remote_pytest_full_summary.json")  # 全量测试树摘要的 retained 路径

# 环境文件记录解释器、平台和工具解析事实。
REMOTE_ENVIRONMENT_JSON = PurePosixPath("remote_environment.json")  # 远端环境原始事实路径

# cwd 文件记录远程进程的实际工作目录和外层身份。
REMOTE_CWD_JSON = PurePosixPath("remote_cwd.json")  # 远端工作目录原始事实路径

# pressure 文件记录阶段、fixture 和 simulator 覆盖压力。
REMOTE_PRESSURE_REPORT_JSON = PurePosixPath("skill_pressure_report.json")  # 实际检查压力报告路径

# archive 文件记录 retained 工件的路径、大小和内容摘要。
REMOTE_ARCHIVE_MANIFEST_JSON = PurePosixPath("validation_archive_manifest.json")  # retained 文件归档清单路径

# evidence 文件汇总三阶段和远程身份哈希。
REMOTE_TEST_EVIDENCE_JSON = PurePosixPath("remote_test_evidence.json")  # 本轮远端测试证据总表路径

# completion.json 只在完整远程链到达末尾后原子生成。
REMOTE_COMPLETION_JSON = PurePosixPath("completion.json")  # smoke 运行目录内的最终完成身份清单

# agent_review.json 位于 runs/<run-id> 根，记录 Agent 对本轮证据的自动审核结论。
REMOTE_AGENT_REVIEW_JSON = PurePosixPath("agent_review.json")  # outer run 根的 Agent 审核文件

# 旧调用默认把 Agent 审核文件放在 reports 的上一级 outer run 根。
REMOTE_DEFAULT_AGENT_REVIEW_PATH = str(PurePosixPath("..") / ".." / REMOTE_AGENT_REVIEW_JSON)  # 兼容旧调用的审核路径

# validation.json 提供主流程 ok、metrics 和产物映射。
REMOTE_EXECUTE_VALIDATION_JSON = REMOTE_EXECUTE_ROOT / "validation.json"  # 主流程 JSON 证据路径

# erie_adapter.v 是 retained run 摘要中的 RTL 人工复核入口。
REMOTE_EXECUTE_RTL_PATH = (REMOTE_EXECUTE_ROOT / "rtl" / "generated" / "rtl" / "erie_adapter.v")  # RTL 产物 retained 地址

# tb_erie_adapter.v 是 retained run 摘要中的仿真激励入口。
REMOTE_EXECUTE_TESTBENCH_PATH = (REMOTE_EXECUTE_ROOT / "rtl" / "generated" / "tb" / "tb_erie_adapter.v")  # 失败复盘的 testbench 入口

# summary.json 汇总四类远端 fixture 的执行状态。
REMOTE_FIXTURE_SUMMARY_JSON = REMOTE_FIXTURE_ROOT / "summary.json"  # fixture 汇总 JSON 证据路径

# simulator 后端枚举必须与 runtime validation 后端名称保持一致。
SIMULATOR_BACKENDS = ("xsim", "vcs_verdi", "iverilog")  # 可持久化的仿真后端

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
    -u VERILOG_GENERATOR_REMOTE_SERVER_ID \\
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

# _prepare_remote_validation_context 解析选项并生成远端命令片段。
def _prepare_remote_validation_context(
    str_remote_skill: str,
    str_remote_python: str,
    dict_options: dict[str, Any],
) -> dict[str, Any]:
    """准备远端 bash 主体所需的全部命令片段。

    :param str_remote_skill: 远端上传后的 skill 工作区路径。
    :param str_remote_python: 远端 Python 命令。
    :param dict_options: 兼容旧关键词的远程执行选项。
    :return: 可供主命令渲染器读取的具名上下文。
    """

    # 从兼容关键词映射中提取本轮 smoke 输出处置策略。
    bool_cleanup_outputs = bool(dict_options.get("cleanup_outputs", False))  # 是否清理远端 smoke 输出

    # 从兼容关键词映射中提取已确认的工具链选择。
    dict_toolchain_selection = dict_options.get("toolchain_selection")  # 已确认的远端工具链选择

    # 从兼容关键词映射中提取 runtime 配置相对路径。
    str_remote_runtime_config_path = dict_options.get("remote_runtime_config_path")  # 失败提示中的配置路径

    # 从兼容关键词映射中提取本轮 outer run 标识。
    str_run_id = str(dict_options.get("run_id", ""))  # 当前 retained run 的唯一标识

    # 从兼容关键词映射中提取上传包源码摘要。
    str_source_digest = str(dict_options.get("source_digest", ""))  # 当前 staging 包的内容摘要

    # 从兼容关键词映射中提取远程服务器标识。
    str_remote_server_id = str(dict_options.get("remote_server_id", ""))  # 当前 SSH 目标 server 身份

    # 新布局把报告目录作为 outer run 的直接子目录传入，旧调用默认 reports。
    str_report_root = str(dict_options.get("report_root", "reports")) or "reports"  # 本轮直接报告目录

    # Agent 审核文件位于 outer run 根，旧调用默认放在 reports 的上一级。
    str_agent_review_path = str(dict_options.get("agent_review_path", REMOTE_DEFAULT_AGENT_REVIEW_PATH))  # Agent 审核文件路径

    # Python 命令进入 shell 前必须单引号转义。
    str_py = sh_quote(str_remote_python)  # 主远端 bash 脚本使用的 Python 命令

    # 报告目录经过 shell quoting，避免路径片段被重新解释。
    str_report_root_quoted = sh_quote(str_report_root)  # 直接报告目录的安全 shell 文本

    # Agent 审核路径独立 quoting，保证 outer run 绑定不被 shell 改写。
    str_agent_review_path_quoted = sh_quote(str_agent_review_path)  # Agent 审核路径的安全 shell 文本

    # cleanup、fixture 和工具链片段均在本地生成后注入主命令。
    str_cleanup_snippet = remote_output_cleanup_snippet(bool_cleanup_outputs, str_remote_python)  # smoke 输出处置脚本片段

    # fixture 名称通过环境变量传给远端 Python 内联脚本。
    str_fixture_names = " ".join(REMOTE_SIMULATION_FIXTURES)  # 合法仿真 fixture 名称串

    # 缺省情况下由远端工具探测选择仿真后端。
    str_selected_vivado = ""  # 未持久化选择时为空的 Vivado settings64.sh 路径

    # 缺省后端为空，表示由远端优先级自动选择。
    str_selected_backend = ""  # 未持久化选择时为空的仿真后端名称

    # 已持久化工具链选择时提取后端和 Vivado 路径。
    if dict_toolchain_selection:

        # Vivado 路径只在 xsim 后端需要。
        str_selected_vivado = str(dict_toolchain_selection.get("vivado_settings64") or "")  # 已确认的 Vivado 激活脚本路径

        # 后端名称用于覆盖远端 simulator priority。
        str_selected_backend = str(dict_toolchain_selection.get("simulator_backend") or "")  # 已确认的仿真后端名称

    # 生成工具链、RTL、文件名和 bytecode 片段。
    str_simulator_priority_snippet = simulator_priority_export_snippet(str_selected_backend)  # 按选定后端生成 simulator priority 导出语句

    # Vivado 激活片段必须在工具探测之前执行。
    str_vivado_snippet = vivado_activation_snippet(  # 工具探测前执行的 Vivado 激活片段
        str_selected_vivado,  # xsim 使用的 settings64.sh 路径
        str_selected_backend,  # 需要激活的 simulator backend 名称
        str_remote_runtime_config_path,  # runtime 配置缺失时的诊断路径
    )

    # RTL Markdown 约束片段独立绑定远端 Python 命令。
    str_rtl_md_snippet = rtl_md_constraint_remote_snippet(str_remote_python)  # RTL Markdown 约束回归脚本

    # 文件名门禁片段独立验证无效文件不会进入 simulator。
    str_filename_gate_snippet = filename_gate_remote_snippet(str_remote_python)  # VG148/VG149 远端回归脚本

    # bytecode 清理片段保持 retained workspace，不删除远端缓存。
    str_bytecode_cleanup = remote_bytecode_cleanup_snippet(str_remote_python)  # retained workspace 的 pycache 处置脚本

    # 三阶段命令和阶段 runner 使用同一经过 quoting 的 Python 命令。
    dict_pytest_commands = _build_remote_pytest_commands(str_py)  # 三阶段命令文本映射

    # 阶段 runner 写出真实 pytest 退出码与摘要。
    str_phase_runner = _build_remote_phase_runner(str_py)  # 阶段命令执行和摘要写入片段

    # 返回主命令渲染器所需的稳定具名上下文。
    return {
        "str_remote_skill": str_remote_skill,
        "str_py": str_py,
        "str_report_root_quoted": str_report_root_quoted,
        "str_agent_review_path_quoted": str_agent_review_path_quoted,
        "str_run_id": str_run_id,
        "str_source_digest": str_source_digest,
        "str_remote_server_id": str_remote_server_id,
        "str_vivado_snippet": str_vivado_snippet,
        "str_simulator_priority_snippet": str_simulator_priority_snippet,
        "str_phase_runner": str_phase_runner,
        "str_targeted_pytest_command": dict_pytest_commands["targeted"],
        "str_regression_pytest_command": dict_pytest_commands["regression"],
        "str_full_pytest_command": dict_pytest_commands["full"],
        "str_rtl_md_snippet": str_rtl_md_snippet,
        "str_filename_gate_snippet": str_filename_gate_snippet,
        "str_fixture_names": str_fixture_names,
        "str_cleanup_snippet": str_cleanup_snippet,
        "str_bytecode_cleanup": str_bytecode_cleanup,
    }

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
        remote_runtime_config_path、run_id、source_digest 和 remote_server_id。
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
export HOME="$VERILOG_GENERATOR_REPORT_ROOT/.validation-home"
export PYTHONPATH="skills/readable-verilog-generator${{PYTHONPATH:+:$PYTHONPATH}}"

# outer run 身份用于把远端所有阶段证据绑定到同一 retained 目录。
export VERILOG_GENERATOR_RUN_ID={sh_quote(dict_context['str_run_id'])}

# 上传包源码摘要用于阻止不同候选复用本轮完成清单。
export VERILOG_GENERATOR_SOURCE_DIGEST={sh_quote(dict_context['str_source_digest'])}

# 服务器标识写入远端事实，便于区分多主机验证结果。
export VERILOG_GENERATOR_REMOTE_SERVER_ID={sh_quote(dict_context['str_remote_server_id'])}

# reports 目录直接承载日志、阶段摘要和后续结构化证据，不再嵌套 smoke_runs_*。
export VERILOG_GENERATOR_SMOKE_RUN_DIR="$VERILOG_GENERATOR_REPORT_ROOT"

# UTC 起始时间用于排序 completion 和 retained 工件的事件线。
export VERILOG_GENERATOR_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR"

# 当前 outer run 的审核文件只能由本轮命令生成，不能沿用上传包或预置目录中的旧身份。
rm -f "$VERILOG_GENERATOR_AGENT_REVIEW_PATH"

# 上传包把隔离 Codex 事实放在 skill 下，启动前复制到 outer reports 的 HOME 映射。
validation_home_source="$PWD/reports/.validation-home"
validation_home_target="$VERILOG_GENERATOR_REPORT_ROOT/.validation-home"
mkdir -p "$validation_home_target"
cp -R "$validation_home_source/." "$validation_home_target/"

# 无论阶段在哪一步失败，都由 Agent 生成 retained 审核文件，避免失败证据只剩散落日志。
write_agent_review_on_exit() {{
  local exit_code="$?"
  if [ ! -f "$VERILOG_GENERATOR_AGENT_REVIEW_PATH" ]; then
    REVIEW_STATUS="failed" REVIEW_EXIT_CODE="$exit_code" {str_py} - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

review_path = Path(os.environ["VERILOG_GENERATOR_AGENT_REVIEW_PATH"])
payload = {{
    "schema": 1,
    "kind": "agent-review",
    "status": os.environ.get("REVIEW_STATUS", "failed"),
    "reviewed_by": "agent",
    "exit_code": int(os.environ.get("REVIEW_EXIT_CODE", "1")),
    "run_id": os.environ.get("VERILOG_GENERATOR_RUN_ID", ""),
    "source_digest": os.environ.get("VERILOG_GENERATOR_SOURCE_DIGEST", ""),
    "remote_server_id": os.environ.get("VERILOG_GENERATOR_REMOTE_SERVER_ID", ""),
    "reviewed_at": datetime.now(timezone.utc).isoformat(),
}}
review_path.parent.mkdir(parents=True, exist_ok=True)
review_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
PY
  fi
  exit "$exit_code"
}}
trap write_agent_review_on_exit EXIT
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

# workflow CLI 的生成目录必须留在 staged skill workspace，完成后再复制到 outer reports 归档。
workflow_workspace_root="$PWD/.smoke-scratch"
workflow_execute_root="$workflow_workspace_root/remote_execute"
workflow_implement_root="$workflow_workspace_root/remote_implement"
mkdir -p "$workflow_workspace_root"

# smoke runner 继续执行 fixture、工具链和 readiness 约束。
{str_py} -m tests.smoke.run_smoke \
  --settings skills/readable-verilog-generator/config/defaults.json \
  --run-dir "$VERILOG_GENERATOR_SMOKE_RUN_DIR"
{dict_context['str_rtl_md_snippet']}
{dict_context['str_filename_gate_snippet']}
if [ -n "$configured_simulator_backend" ]; then
  export VERILOG_GENERATOR_SIMULATOR_PRIORITY="$configured_simulator_backend"
  expected_sim_backend="$configured_simulator_backend"
fi
{str_py} -m {WORKFLOW_CLI_MODULE} run-workflow \
  --spec skills/readable-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
  --out-dir "$workflow_execute_root" \
  --model-provider mock \
  --readiness execute \
  --external-target local
{str_py} -m {WORKFLOW_CLI_MODULE} validate \
  --spec skills/readable-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
  --path "$workflow_execute_root/attempt-001/rtl/generated" \
  --readiness execute \
  --external-target local
mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_execute"
cp -R "$workflow_execute_root/." "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_execute/"
EXPECTED_SIM_BACKEND="$expected_sim_backend" {str_py} - <<'PY'
import json
import os
from pathlib import Path
expected = os.environ["EXPECTED_SIM_BACKEND"]
smoke_root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"])
validation = json.loads((smoke_root / "remote_execute/attempt-001/validation.json").read_text(encoding="utf-8"))
metrics = validation["metrics"]
assert metrics["selected_simulator_backend"] == expected, metrics
assert set(["xvlog", "xelab", "xsim"]).issubset(metrics["executed_tools"]) if expected == "xsim" else True, metrics
if expected == "iverilog":
    assert "xsim" in metrics["missing_preferred_backends"], metrics
    assert "vcs_verdi" in metrics["missing_preferred_backends"], metrics
PY
mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_fixtures"
REMOTE_FIXTURES="{dict_context['str_fixture_names']}" EXPECTED_SIM_BACKEND="$expected_sim_backend" {str_py} - <<'PY'
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

WORKFLOW_CLI_MODULE = "scripts.python.workflow.cli"

fixtures = os.environ["REMOTE_FIXTURES"].split()
expected = os.environ["EXPECTED_SIM_BACKEND"]
smoke_root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"])
fixture_workspace_root = Path(".smoke-scratch/remote_fixtures")
fixture_archive_root = smoke_root / "remote_fixtures"
fixture_workspace_root.mkdir(parents=True, exist_ok=True)
fixture_archive_root.mkdir(parents=True, exist_ok=True)
summary = {{"fixtures": []}}
bad_source = Path(
    "skills/readable-verilog-generator/assets/examples/remote_fixtures/"
    "comb_operation_budget/comb_operation_budget_bad.v"
)
bad_report = fixture_workspace_root / "comb_operation_budget/bad_quality_gate.json"
bad_markdown = fixture_workspace_root / "comb_operation_budget/bad_quality_gate.md"
bad_report.parent.mkdir(parents=True, exist_ok=True)
bad_command = [
    sys.executable,
    "-m",
    "scripts.python.quality.verilog_quality_gate",
    str(bad_source),
    "--json",
    str(bad_report),
    "--markdown",
    str(bad_markdown),
]
bad_result = subprocess.run(bad_command, check=False)
assert bad_result.returncode != 0, bad_result.returncode
bad_payload = json.loads(bad_report.read_text(encoding="utf-8"))
bad_vg146 = next(
    item for item in bad_payload["vg_rule_results"] if item["gate_id"] == "VG146"
)
assert bad_payload["ok"] is False, bad_payload
assert bad_vg146["status"] == "failed", bad_vg146
hierarchy_root = Path(
    "skills/readable-verilog-generator/assets/examples/remote_fixtures/comb_hierarchy_budget"
)
hierarchy_probes = (
    ("hierarchy_within_budget.v", "VG146", "passed", None, None),
    ("hierarchy_over_budget.v", "VG146", "failed", 4, "hierarchy_2_plus_2/u_child"),
    ("hierarchy_q_cut.v", "VG146", "passed", None, None),
    ("hierarchy_child_loop.v", "VG147", "failed", 4, "hierarchy_child_loop/u_child"),
)
for source_name, gate_id, expected_status, expected_count, expected_path in hierarchy_probes:
    source_path = hierarchy_root / source_name
    probe_report = fixture_workspace_root / "comb_hierarchy_budget" / (
        source_path.stem + "_quality_gate.json"
    )
    probe_markdown = probe_report.with_suffix(".md")
    probe_report.parent.mkdir(parents=True, exist_ok=True)
    probe_result = subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.python.quality.verilog_quality_gate",
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
        evidence = "\\n".join(item["evidence"] for item in gate_result["findings"])
        assert f"operation_count={{expected_count}}" in evidence, evidence
        assert expected_path in evidence, evidence
for name in fixtures:
    source_root = Path("skills/readable-verilog-generator/assets/examples/remote_fixtures") / name
    staged_root = fixture_workspace_root / name
    generated = staged_root / "generated"
    shutil.copytree(source_root / "generated", generated, dirs_exist_ok=True)
    staged_testbench = generated / "tb" / ("tb_" + name + ".v")
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
  {str_py} -m {WORKFLOW_CLI_MODULE} run-workflow \
    --spec skills/readable-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
    --out-dir "$workflow_implement_root" \
    --model-provider mock \
    --readiness implement \
    --external-target local
  mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_implement"
  cp -R "$workflow_implement_root/." "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_implement/"
  {str_py} - <<'PY'
import json
import os
from pathlib import Path
smoke_root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"])
result = json.loads((smoke_root / "remote_implement/workflow_result.json").read_text(encoding="utf-8"))
assert result["status"] == "passed", result
PY
else
  set +e
  {str_py} -m {WORKFLOW_CLI_MODULE} run-workflow \
    --spec skills/readable-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
    --out-dir "$workflow_implement_root" \
    --model-provider mock \
    --readiness implement \
    --external-target local
  impl_status=$?
  set -e
  mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_implement"
  cp -R "$workflow_implement_root/." "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_implement/"
  if [ "$impl_status" -eq 0 ]; then
    echo "Expected implement readiness to block when yosys is missing." >&2
    exit 1
  fi
  {str_py} - <<'PY'
import json
import os
from pathlib import Path
smoke_root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"])
result = json.loads((smoke_root / "remote_implement/workflow_result.json").read_text(encoding="utf-8"))
assert result["status"] == "blocked_toolchain", result
validation = json.loads((smoke_root / "remote_implement/attempt-001/validation.json").read_text(encoding="utf-8"))
assert any(
    item.get("tool") == "yosys" and item.get("source") == "toolchain_issue"
    for item in validation["issues"]
), validation
PY
fi

# runtime 模块汇总环境、cwd、压力、归档和三阶段测试事实。
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

# filename_gate_remote_snippet 生成 VG148/VG149 远端交付门与 xsim 准入片段。
def filename_gate_remote_snippet(str_remote_python: str) -> str:
    """生成远端文件名门禁与合法 testbench 仿真片段。

    :param str_remote_python: 远端 Python 命令。
    :return: 可嵌入主 bash 脚本的文件名回归片段。
    """

    # 文件名 probe 的解释器名称先做 shell quoting，避免破坏 heredoc 前缀。
    str_py = sh_quote(str_remote_python)  # 文件名门禁片段使用的 Python 命令

    # 所有故意违规文件只进入 generated-deliverable gate，不进入 simulator。
    str_template = r"""
mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_fixtures/file_naming_gates"
__PY__ - <<'PY'
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(".smoke-scratch/remote_fixtures/file_naming_gates")
archive_root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"]) / "remote_fixtures/file_naming_gates"
root.mkdir(parents=True, exist_ok=True)
archive_root.mkdir(parents=True, exist_ok=True)
cases = (
    {
        "case_id": "vg148_version_suffix_reject",
        "filename": "module_v1.v",
        "gate_id": "VG148",
        "command_contract": "generated_deliverable_gate --spec --json --markdown",
        "source": "module module_v1;\nendmodule\n",
        "spec": {},
        "expected_status": "failed",
        "expected_role": "design",
        "expected_role_source": "content_evidence",
        "confirmation_required": False,
        "confirmed_role": None,
    },
    {
        "case_id": "vg148_numeric_suffix_reject",
        "filename": "module_123.v",
        "gate_id": "VG148",
        "command_contract": "generated_deliverable_gate --spec --json --markdown",
        "source": "module module_123;\nendmodule\n",
        "spec": {},
        "expected_status": "failed",
        "expected_role": "design",
        "expected_role_source": "content_evidence",
        "confirmation_required": False,
        "confirmed_role": None,
    },
    {
        "case_id": "vg148_protocol_digit_allow",
        "filename": "axi4_lite.v",
        "gate_id": "VG148",
        "command_contract": "generated_deliverable_gate --spec --json --markdown",
        "source": "module axi4_lite;\nendmodule\n",
        "spec": {},
        "expected_status": "passed",
        "expected_role": "design",
        "expected_role_source": "content_evidence",
        "confirmation_required": False,
        "confirmed_role": None,
    },
    {
        "case_id": "vg149_suffix_tb_reject",
        "filename": "counter_tb.v",
        "gate_id": "VG149",
        "command_contract": "generated_deliverable_gate --spec --json --markdown",
        "source": "module counter_tb;\nendmodule\n",
        "spec": {},
        "expected_status": "failed",
        "expected_role": "testbench",
        "expected_role_source": "explicit_name",
        "confirmation_required": False,
        "confirmed_role": None,
    },
    {
        "case_id": "vg149_counter_ambiguous",
        "filename": "counter.v",
        "gate_id": "VG149",
        "command_contract": "generated_deliverable_gate --spec --json --markdown",
        "source": "module counter();\ninitial begin\n    #1;\n    $finish;\nend\nendmodule\n",
        "spec": {},
        "expected_status": "inconclusive",
        "expected_role": "ambiguous",
        "expected_role_source": "content_evidence",
        "confirmation_required": True,
        "confirmed_role": None,
    },
    {
        "case_id": "vg149_counter_confirmed_testbench_reject",
        "filename": "counter.v",
        "gate_id": "VG149",
        "command_contract": "generated_deliverable_gate --spec --json --markdown",
        "source": "module counter();\ninitial begin\n    #1;\n    $finish;\nend\nendmodule\n",
        "spec": {"file_role_confirmations": {"counter.v": "testbench"}},
        "expected_status": "failed",
        "expected_role": "testbench",
        "expected_role_source": "confirmed",
        "confirmation_required": False,
        "confirmed_role": "testbench",
    },
    {
        "case_id": "vg149_tb_prefix_allow",
        "filename": "tb_counter.v",
        "gate_id": "VG149",
        "command_contract": "generated_deliverable_gate --spec --json --markdown",
        "source": "module tb_counter;\ninitial begin\n    $finish;\nend\nendmodule\n",
        "spec": {},
        "expected_status": "passed",
        "expected_role": "testbench",
        "expected_role_source": "explicit_name",
        "confirmation_required": False,
        "confirmed_role": None,
    },
)


def find_named_list(value, key):
    if isinstance(value, dict):
        if isinstance(value.get(key), list):
            return value[key]
        for child in value.values():
            found = find_named_list(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_named_list(child, key)
            if found is not None:
                return found
    return None


summary = {"cases": []}
for case in cases:
    case_root = root / case["case_id"]
    source_root = case_root / "source"
    source_root.mkdir(parents=True, exist_ok=True)
    (source_root / case["filename"]).write_text(case["source"], encoding="utf-8")
    spec_path = case_root / "spec.json"
    spec_path.write_text(json.dumps(case["spec"], indent=2, sort_keys=True), encoding="utf-8")
    report_path = case_root / "report.json"
    markdown_path = case_root / "report.md"
    command = [
        sys.executable,
        "-m",
        "scripts.python.validation.generated_deliverable_gate",
        str(source_root),
        "--spec",
        str(spec_path),
        "--json",
        str(report_path),
        "--markdown",
        str(markdown_path),
    ]
    required_command_tokens = case["command_contract"].split()
    assert required_command_tokens[0] in command[2], command
    assert all(token in command for token in required_command_tokens[1:]), command
    result = subprocess.run(command, check=False)
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    gate_results = find_named_list(payload, "vg_rule_results") or []
    gate_result = next(item for item in gate_results if item["gate_id"] == case["gate_id"])
    file_facts = find_named_list(payload, "file_facts") or []
    file_fact = next(item for item in file_facts if item["path"] == case["filename"])
    assert gate_result["status"] == case["expected_status"], gate_result
    assert file_fact["role"] == case["expected_role"], file_fact
    assert file_fact["role_source"] == case["expected_role_source"], file_fact
    assert isinstance(file_fact["role_evidence"], list), file_fact
    assert file_fact["confirmation_required"] is case["confirmation_required"], file_fact
    assert file_fact["confirmed_role"] == case["confirmed_role"], file_fact
    if case["expected_status"] != "passed":
        assert result.returncode != 0, result.returncode
    summary["cases"].append(
        {
            "case_id": case["case_id"],
            "gate_id": case["gate_id"],
            "status": gate_result["status"],
            "role": file_fact["role"],
            "role_source": file_fact["role_source"],
            "role_evidence": file_fact["role_evidence"],
            "confirmation_required": file_fact["confirmation_required"],
            "confirmed_role": file_fact["confirmed_role"],
            "report": str(archive_root / case["case_id"] / "report.json"),
            "markdown": str(archive_root / case["case_id"] / "report.md"),
        }
    )
shutil.copytree(root, archive_root, dirs_exist_ok=True)
(archive_root / "summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True),
    encoding="utf-8",
)
PY
mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_fixtures/file_naming_gates/xsim"
cat > "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_fixtures/file_naming_gates/xsim/counter.v" <<'VERILOG'
module counter (
    input wire i_clk,
    input wire i_rstn,
    output reg o_count
);
always @(posedge i_clk or negedge i_rstn) begin
    if (!i_rstn) begin
        o_count <= 1'b0;
    end else begin
        o_count <= ~o_count;
    end
end
endmodule
VERILOG
cat > "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_fixtures/file_naming_gates/xsim/tb_counter.v" <<'VERILOG'
module tb_counter;
reg i_clk;
reg i_rstn;
wire o_count;
counter dut (
    .i_clk(i_clk),
    .i_rstn(i_rstn),
    .o_count(o_count)
);
initial begin
    i_clk = 1'b0;
    forever #5 i_clk = ~i_clk;
end
initial begin
    i_rstn = 1'b0;
    #12 i_rstn = 1'b1;
    #30;
    if ((o_count !== 1'b0) && (o_count !== 1'b1)) begin
        $display("[TB_ERROR] counter output is unknown");
        $finish;
    end
    $display("[TB_PASS] tb_counter completed");
    $finish;
end
endmodule
VERILOG
if [ "$expected_sim_backend" = "xsim" ]; then
  (
    cd "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_fixtures/file_naming_gates/xsim"
    xvlog counter.v tb_counter.v
    xelab tb_counter -s tb_counter_snapshot
    xsim tb_counter_snapshot -runall
  )
fi
""".strip()

    # 唯一解释器占位符替换完成后，文件名 probe 才能嵌入主脚本。
    return str_template.replace("__PY__", str_py)

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
assert catalog["total_rules"] == 127, catalog
assert catalog["active_rules"] == 127, catalog
assert catalog["reserved_rules"] == 0, catalog
prompt = render_prompt(spec(), stage="rtl")
for marker in (
    "Verilog quality gates",
    "VG072",
    "VG111",
    "VG145",
    "VG147",
    "VG148",
    "VG149",
):
    assert marker in prompt, marker
summary = summarize_constraints_for_prompt(max_rules_per_group=3)
assert "127 active gates" in summary, summary
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

    # 新运行目录是审计归档的一部分，任何远端验证流程都保留 reports 及失败证据。
    if cleanup_outputs:

        # 受控清理入口仍校验完整 direct reports 边界，但不删除本轮归档。
        str_py = sh_quote(str_remote_python)  # 安全归档校验片段使用的 Python 命令

        # 归档校验只接受固定的 runs/validation_*/reports 层级，拒绝任意越界路径。
        return f"""{str_py} - <<'PY'
import os
import re
from pathlib import Path

path_reports = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"]).resolve()
if path_reports.name != "reports":
    raise SystemExit(f"Refusing to archive unexpected report path: {{path_reports}}")

path_run = path_reports.parent
if not re.fullmatch(r"validation_[A-Za-z0-9._-]+", path_run.name):
    raise SystemExit(f"Refusing to archive unexpected validation run: {{path_run}}")

if path_run.parent.name != "runs":
    raise SystemExit(f"Refusing to archive outside runs directory: {{path_run}}")

if path_run.parent.parent.name != ".readable-verilog-generator":
    raise SystemExit(f"Refusing to archive outside readable-verilog-generator root: {{path_run}}")

print(f"remote_outputs_retained={{path_reports}} cleanup_request_ignored=archive_policy")
PY"""

    # 默认保留远端输出，方便用户复查 retained run。
    return 'echo "remote_outputs_retained=$VERILOG_GENERATOR_SMOKE_RUN_DIR"'

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
) -> str:
    """生成远端 Vivado settings64.sh 发现和激活脚本。

    :param str_selected_vivado: 用户确认的 settings64.sh 绝对路径。
    :param str_selected_backend: 用户确认的仿真后端。
    :param remote_runtime_config_path: 用于提示用户持久化选择的配置路径。
    :return: 可嵌入主 bash 脚本的 Vivado 激活片段。
    """

    # 提示路径用于多版本 Vivado 时告诉用户写入哪个远端配置文件。
    str_config_hint = str(  # 多版本 Vivado 阻断提示中的配置路径
        remote_runtime_config_path or remote_runtime_settings_relpath()  # 多版本 Vivado 时用户需更新的 runtime 配置路径
    )

    # 非 xsim 后端不需要 Vivado 激活。
    if str_selected_backend and str_selected_backend != "xsim":

        # 仍输出短状态，便于远端日志说明为何跳过 Vivado。
        return "echo 'vivado_settings=not_required_for_selected_backend'"

    # 返回保守发现逻辑：多候选时阻断并要求用户确认。
    return f"""
selected_vivado_settings={sh_quote(str_selected_vivado)}
toolchain_config_hint={sh_quote(str_config_hint)}
vivado_candidates_file="$(mktemp)"
for candidate in \
  "${{XILINX_VIVADO:-}}/settings64.sh" \
  "${{XILINX_VIVADO:-}}/../settings64.sh" \
  /tools/Xilinx/Vivado/*/settings64.sh \
  /tools/Xilinx/Vitis/*/settings64.sh \
  /opt/Xilinx/Vivado/*/settings64.sh; do
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
