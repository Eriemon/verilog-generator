"""远端验证 shell 片段、常量和 POSIX 路径辅助函数。"""

# 标准库负责命令摘要和延迟导入 runtime 配置路径 helper。
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

# completion.json 只在完整远程链到达末尾后原子生成。
REMOTE_COMPLETION_JSON = PurePosixPath("completion.json")  # smoke 运行目录内的最终完成身份清单

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

# remote_validation_command 拼装远端 bash 验证脚本。
def remote_validation_command(
    str_remote_skill: str,
    str_remote_python: str,
    *,
    cleanup_outputs: bool = False,
    toolchain_selection: dict[str, Any] | None = None,
    remote_runtime_config_path: str | None = None,
    run_id: str = "",
    source_digest: str = "",
) -> str:
    """生成远端执行的 bash 信心门禁脚本。

    :param str_remote_skill: 远端上传后的 skill 工作区路径。
    :param str_remote_python: 远端 Python 命令。
    :param cleanup_outputs: 是否清理远端 smoke 输出。
    :param toolchain_selection: 已确认的远端工具链选择。
    :param remote_runtime_config_path: 远端 runtime 配置相对路径。
    :param run_id: 当前 outer retained run 的唯一标识。
    :param source_digest: 本次上传源码包的 SHA-256 摘要。
    :return: 可交给 `bash -lc` 执行的脚本文本。
    """

    # Python 命令进入 shell 前必须单引号转义。
    str_py = sh_quote(str_remote_python)  # 主远端 bash 脚本使用的 Python 命令

    # cleanup 片段根据用户是否要求删除远端输出决定。
    str_cleanup_snippet = remote_output_cleanup_snippet(cleanup_outputs, str_remote_python)  # smoke 输出处置脚本片段

    # fixture 名称通过环境变量传给远端 Python 内联脚本。
    str_fixture_names = " ".join(REMOTE_SIMULATION_FIXTURES)  # 只含合法仿真 fixture 的名称

    # 缺省情况下由远端工具探测选择仿真后端。
    str_selected_vivado = ""  # 未持久化选择时为空的 Vivado settings64.sh 路径

    # 缺省后端为空，表示按 xsim/vcs_verdi/iverilog 优先级自动选择。
    str_selected_backend = ""  # 未持久化选择时为空的仿真后端名称

    # 已持久化工具链选择时提取后端和 Vivado 路径。
    if toolchain_selection:

        # Vivado 路径只在 xsim 后端需要。
        str_selected_vivado = str(toolchain_selection.get("vivado_settings64") or "")  # 已确认的 Vivado 激活脚本路径

        # 后端名称用于覆盖 VERILOG_GENERATOR_SIMULATOR_PRIORITY。
        str_selected_backend = str(toolchain_selection.get("simulator_backend") or "")  # 已确认的仿真后端名称

    # simulator priority 片段负责导出配置后端。
    str_simulator_priority_snippet = simulator_priority_export_snippet(str_selected_backend)  # 导出 simulator priority 的 shell 片段

    # Vivado 激活片段必须在工具探测之前执行。
    str_vivado_snippet = vivado_activation_snippet(  # 工具探测前执行的 Vivado 激活片段
        str_selected_vivado,  # 用户确认的 settings64.sh 路径
        str_selected_backend,  # 用户确认的 simulator backend
        remote_runtime_config_path,  # 多 Vivado 候选提示中的配置位置
    )

    # RTL Markdown 约束远端回归独立生成片段。
    str_rtl_md_snippet = rtl_md_constraint_remote_snippet(str_remote_python)  # RTL Markdown 约束回归脚本

    # 文件名门禁 probe 独立验证无效文件不会进入 simulator。
    str_filename_gate_snippet = filename_gate_remote_snippet(str_remote_python)  # VG148/VG149 远端回归脚本

    # bytecode 清理当前保持 retained workspace，不删除远端缓存。
    str_bytecode_cleanup = remote_bytecode_cleanup_snippet(str_remote_python)  # 远端执行结束后的 pycache 保留/清理片段

    # 先生成不含完成清单的主命令体，命令摘要以此稳定文本为准。
    str_command_body = f"""
set -eu
set -o pipefail
cd {sh_quote(str_remote_skill)}
export HOME="$PWD/reports/.validation-home"
export PYTHONPATH="skills/readable-verilog-generator${{PYTHONPATH:+:$PYTHONPATH}}"
export VERILOG_GENERATOR_SMOKE_RUN_DIR="reports/smoke_runs_$(date +%Y%m%d-%H%M%S-%6N)_$$"
export VERILOG_GENERATOR_STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR"
{str_py} --version
{str_vivado_snippet}
{str_simulator_priority_snippet}
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
{str_py} -m pytest -q -p no:cacheprovider 2>&1 | tee "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_pytest.log"
{str_py} -m scripts.python.remote.remote_pytest_summary
{str_py} -m tests.smoke.run_smoke \
  --settings skills/readable-verilog-generator/config/defaults.json \
  --run-dir "$VERILOG_GENERATOR_SMOKE_RUN_DIR"
{str_rtl_md_snippet}
{str_filename_gate_snippet}
if [ -n "$configured_simulator_backend" ]; then
  export VERILOG_GENERATOR_SIMULATOR_PRIORITY="$configured_simulator_backend"
  expected_sim_backend="$configured_simulator_backend"
fi
{str_py} -m {WORKFLOW_CLI_MODULE} run-workflow \
  --spec skills/readable-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
  --out-dir "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_execute" \
  --model-provider mock \
  --readiness execute \
  --external-target local
{str_py} -m {WORKFLOW_CLI_MODULE} validate \
  --spec skills/readable-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
  --path "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_execute/attempt-001/rtl/generated" \
  --readiness execute \
  --external-target local
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
REMOTE_FIXTURES="{str_fixture_names}" EXPECTED_SIM_BACKEND="$expected_sim_backend" {str_py} - <<'PY'
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
summary = {{"fixtures": []}}
bad_source = Path(
    "skills/readable-verilog-generator/assets/examples/remote_fixtures/"
    "comb_operation_budget/comb_operation_budget_bad.v"
)
bad_report = smoke_root / "remote_fixtures/comb_operation_budget/bad_quality_gate.json"
bad_markdown = smoke_root / "remote_fixtures/comb_operation_budget/bad_quality_gate.md"
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
    probe_report = smoke_root / "remote_fixtures/comb_hierarchy_budget" / (
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
    staged_root = smoke_root / "remote_fixtures" / name
    generated = staged_root / "generated"
    shutil.copytree(source_root / "generated", generated, dirs_exist_ok=True)
    staged_testbench = generated / "tb" / ("tb_" + name + ".v")
    spec_payload = json.loads((source_root / "spec.json").read_text(encoding="utf-8"))
    for output in spec_payload.get("outputs", []):
        if output.get("kind") == "testbench":
            output["path"] = "tb/" + staged_testbench.name
    spec = staged_root / "spec.json"
    spec.write_text(json.dumps(spec_payload, indent=2, sort_keys=True), encoding="utf-8")
    report_json = smoke_root / "remote_fixtures" / name / "validation.json"
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
    outputs = report.get("spec_outputs", [])
    summary["fixtures"].append({{
        "name": name,
        "ok": report["ok"],
        "selected_simulator_backend": metrics["selected_simulator_backend"],
        "executed_tools": metrics["executed_tools"],
        "rtl_path": str(generated / "rtl" / (name + ".v")),
        "testbench_path": str(staged_testbench),
        "validation_json": str(report_json),
        "outputs": outputs,
    }})
(smoke_root / "remote_fixtures/summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True),
    encoding="utf-8",
)
PY
if [ "$yosys_available" -eq 1 ]; then
  {str_py} -m {WORKFLOW_CLI_MODULE} run-workflow \
    --spec skills/readable-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
    --out-dir "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_implement" \
    --model-provider mock \
    --readiness implement \
    --external-target local
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
    --out-dir "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_implement" \
    --model-provider mock \
    --readiness implement \
    --external-target local
  impl_status=$?
  set -e
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
""".strip()

    # 主命令摘要绑定本次实际执行合同，避免完成清单只证明了文件存在。
    str_command_digest = hashlib.sha256(str_command_body.encode("utf-8")).hexdigest()  # 远程主命令体 SHA-256

    # 最终写入器只在主命令全部成功后执行，并通过临时文件替换保证原子可见。
    str_completion_snippet = f"""
VERILOG_GENERATOR_RUN_ID={sh_quote(run_id)} \\
VERILOG_GENERATOR_SOURCE_DIGEST={source_digest} \\
VERILOG_GENERATOR_COMMAND_DIGEST={str_command_digest} \\
{str_py} - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

smoke_root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"])
completion_path = smoke_root / "completion.json"
temporary_path = smoke_root / "completion.json.tmp"
payload = {{
    "status": "passed",
    "run_id": os.environ["VERILOG_GENERATOR_RUN_ID"],
    "source_digest": os.environ["VERILOG_GENERATOR_SOURCE_DIGEST"],
    "command_digest": os.environ["VERILOG_GENERATOR_COMMAND_DIGEST"],
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
            str_cleanup_snippet,
            str_bytecode_cleanup,
        )
    )

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
import subprocess
import sys
from pathlib import Path

root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"]) / "remote_fixtures/file_naming_gates"
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
            "report": str(report_path),
            "markdown": str(markdown_path),
        }
    )
(root / "summary.json").write_text(
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
bad_dir = smoke_root / "remote_verilog_quality_gates/bad"
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

good_dir = smoke_root / "remote_verilog_quality_gates/good"
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
PY
__PY__ -m scripts.python.workflow.cli eval-skill \
  --evals skills/readable-verilog-generator/evals/evals.json \
  --out "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_eval_skill.json" \
  --no-state
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

    # 用户要求清理时使用 Python 安全删除，避免 shell rm -rf。
    if cleanup_outputs:

        # 清理片段中的 Python 命令同样需要 shell 单引号转义。
        str_py = sh_quote(str_remote_python)  # 安全清理片段使用的 Python 命令

        # 返回安全清理片段，测试断言其中不含 rm -rf。
        return f"""{str_py} - <<'PY'
import os
import shutil
from pathlib import Path

path = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"]).resolve()
reports_root = Path("reports").resolve()
if path.parent != reports_root or not path.name.startswith("smoke_runs_"):
    raise SystemExit(f"Refusing to remove unexpected smoke output path: {{path}}")
if path.exists():
    if not path.is_dir():
        raise SystemExit(f"Refusing to remove non-directory output path: {{path}}")
    shutil.rmtree(path)
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
