"""远端验证 shell 片段、常量和 POSIX 路径辅助函数。"""

# 标准库只用于延迟导入 runtime 配置路径 helper。
import sys

# pathlib 负责远端 retained run 常量中的 POSIX 路径。
from pathlib import Path, PurePosixPath

# Any 只用于工具链选择字典这类异构载荷。
from typing import Any

# skill 主体根目录供脚本直运行时定位 runtime 包。
PATH_SKILL_ROOT = Path(__file__).resolve().parents[3]  # 包含 runtime、scripts 与 config 的 skill 根目录

# workflow CLI 统一切到 scripts/python/workflow 官方模块入口。
WORKFLOW_CLI_MODULE = "scripts.python.workflow.cli"  # workflow 官方 CLI 模块名

# 远端固定 fixture 覆盖组合逻辑、流水线和 ready-valid 协议。
REMOTE_FIXTURES = (  # 远端内联 fixture 脚本生成三类 RTL 回归用例
    "comb_parity_mux",  # 覆盖组合奇偶校验与 mux 输出选择链路
    "pipeline_delay",  # 覆盖多拍寄存器延迟和复位后的数据推进
    "ready_valid_slice",  # 覆盖 ready-valid 反压握手与数据保持约束
)

# remote_execute attempt-001 是远端主流程的稳定证据根。
REMOTE_EXECUTE_ROOT = PurePosixPath("_smoke_runs") / "remote_execute" / "attempt-001"  # 主流程证据相对根

# remote_fixtures 保存三类小用例聚合报告。
REMOTE_FIXTURE_ROOT = PurePosixPath("_smoke_runs") / "remote_fixtures"  # fixture 证据相对根

# remote_pytest_summary.json 保存权威远程 pytest 的精确计数和耗时。
REMOTE_PYTEST_SUMMARY_JSON = PurePosixPath("_smoke_runs") / "remote_pytest_summary.json"  # pytest 结构化证据路径

# validation.json 提供主流程 ok、metrics 和产物映射。
REMOTE_EXECUTE_VALIDATION_JSON = REMOTE_EXECUTE_ROOT / "validation.json"  # 主流程 JSON 证据路径

# erie_adapter.v 是 retained run 摘要中的 RTL 人工复核入口。
REMOTE_EXECUTE_RTL_PATH = (REMOTE_EXECUTE_ROOT / "rtl" / "generated" / "rtl" / "erie_adapter.v")  # RTL 产物 retained 地址

# erie_adapter_tb.v 是 retained run 摘要中的仿真激励入口。
REMOTE_EXECUTE_TESTBENCH_PATH = (REMOTE_EXECUTE_ROOT / "rtl" / "generated" / "tb" / "erie_adapter_tb.v")  # 失败复盘的 testbench 入口

# summary.json 汇总三类远端 fixture 的执行状态。
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
) -> str:
    """生成远端执行的 bash 信心门禁脚本。

    :param str_remote_skill: 远端上传后的 skill 工作区路径。
    :param str_remote_python: 远端 Python 命令。
    :param cleanup_outputs: 是否清理远端 smoke 输出。
    :param toolchain_selection: 已确认的远端工具链选择。
    :param remote_runtime_config_path: 远端 runtime 配置相对路径。
    :return: 可交给 `bash -lc` 执行的脚本文本。
    """

    # Python 命令进入 shell 前必须单引号转义。
    str_py = sh_quote(str_remote_python)  # 主远端 bash 脚本使用的 Python 命令

    # cleanup 片段根据用户是否要求删除远端输出决定。
    str_cleanup_snippet = remote_output_cleanup_snippet(cleanup_outputs, str_remote_python)  # smoke 输出处置脚本片段

    # fixture 名称通过环境变量传给远端 Python 内联脚本。
    str_fixture_names = " ".join(REMOTE_FIXTURES)  # 空格分隔的 fixture 名称

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

    # bytecode 清理当前保持 retained workspace，不删除远端缓存。
    str_bytecode_cleanup = remote_bytecode_cleanup_snippet(str_remote_python)  # 远端执行结束后的 pycache 保留/清理片段

    # 返回完整远端 bash 脚本，关键字符串由 smoke 测试断言。
    return f"""
set -eu
set -o pipefail
cd {sh_quote(str_remote_skill)}
export HOME="$PWD/reports/.validation-home"
export PYTHONPATH="skills/readable-verilog-generator${{PYTHONPATH:+:$PYTHONPATH}}"
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
{str_py} -m pytest -q -p no:cacheprovider 2>&1 | tee reports/remote_pytest.log
mkdir -p _smoke_runs
mv reports/remote_pytest.log _smoke_runs/remote_pytest.log
{str_py} -m scripts.python.remote.remote_pytest_summary
{str_py} -m tests.smoke.run_smoke --settings skills/readable-verilog-generator/config/defaults.json
{str_rtl_md_snippet}
if [ -n "$configured_simulator_backend" ]; then
  export VERILOG_GENERATOR_SIMULATOR_PRIORITY="$configured_simulator_backend"
  expected_sim_backend="$configured_simulator_backend"
fi
{str_py} -m {WORKFLOW_CLI_MODULE} run-workflow \
  --spec skills/readable-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
  --out-dir _smoke_runs/remote_execute \
  --model-provider mock \
  --readiness execute \
  --external-target local
{str_py} -m {WORKFLOW_CLI_MODULE} validate \
  --spec skills/readable-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
  --path _smoke_runs/remote_execute/attempt-001/rtl/generated \
  --readiness execute \
  --external-target local
EXPECTED_SIM_BACKEND="$expected_sim_backend" {str_py} - <<'PY'
import json
import os
from pathlib import Path
expected = os.environ["EXPECTED_SIM_BACKEND"]
validation = json.loads(Path("_smoke_runs/remote_execute/attempt-001/validation.json").read_text(encoding="utf-8"))
metrics = validation["metrics"]
assert metrics["selected_simulator_backend"] == expected, metrics
assert set(["xvlog", "xelab", "xsim"]).issubset(metrics["executed_tools"]) if expected == "xsim" else True, metrics
if expected == "iverilog":
    assert "xsim" in metrics["missing_preferred_backends"], metrics
    assert "vcs_verdi" in metrics["missing_preferred_backends"], metrics
PY
mkdir -p _smoke_runs/remote_fixtures
REMOTE_FIXTURES="{str_fixture_names}" EXPECTED_SIM_BACKEND="$expected_sim_backend" {str_py} - <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

WORKFLOW_CLI_MODULE = "scripts.python.workflow.cli"

fixtures = os.environ["REMOTE_FIXTURES"].split()
expected = os.environ["EXPECTED_SIM_BACKEND"]
summary = {{"fixtures": []}}
for name in fixtures:
    spec = Path("skills/readable-verilog-generator/assets/examples/remote_fixtures") / name / "spec.json"
    generated = Path("skills/readable-verilog-generator/assets/examples/remote_fixtures") / name / "generated"
    report_json = Path("_smoke_runs/remote_fixtures") / name / "validation.json"
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
        "testbench_path": str(generated / "tb" / (name + "_tb.v")),
        "validation_json": str(report_json),
        "outputs": outputs,
    }})
Path("_smoke_runs/remote_fixtures/summary.json").write_text(
    json.dumps(summary, indent=2, sort_keys=True),
    encoding="utf-8",
)
PY
if [ "$yosys_available" -eq 1 ]; then
  {str_py} -m {WORKFLOW_CLI_MODULE} run-workflow \
    --spec skills/readable-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
    --out-dir _smoke_runs/remote_implement \
    --model-provider mock \
    --readiness implement \
    --external-target local
  {str_py} - <<'PY'
import json
from pathlib import Path
result = json.loads(Path("_smoke_runs/remote_implement/workflow_result.json").read_text(encoding="utf-8"))
assert result["status"] == "passed", result
PY
else
  set +e
  {str_py} -m {WORKFLOW_CLI_MODULE} run-workflow \
    --spec skills/readable-verilog-generator/assets/examples/rtl_erie_verilog_spec.json \
    --out-dir _smoke_runs/remote_implement \
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
from pathlib import Path
result = json.loads(Path("_smoke_runs/remote_implement/workflow_result.json").read_text(encoding="utf-8"))
assert result["status"] == "blocked_toolchain", result
validation = json.loads(Path("_smoke_runs/remote_implement/attempt-001/validation.json").read_text(encoding="utf-8"))
assert any(
    item.get("tool") == "yosys" and item.get("source") == "toolchain_issue"
    for item in validation["issues"]
), validation
PY
fi
{str_cleanup_snippet}
{str_bytecode_cleanup}
""".strip()

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
mkdir -p _smoke_runs/remote_verilog_quality_gates
__PY__ - <<'PY'
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
assert catalog["total_rules"] == 123, catalog
assert catalog["active_rules"] == 123, catalog
assert catalog["reserved_rules"] == 0, catalog
prompt = render_prompt(spec(), stage="rtl")
for marker in (
    "Verilog quality gates",
    "VG072",
    "VG111",
    "VG145",
):
    assert marker in prompt, marker
summary = summarize_constraints_for_prompt(max_rules_per_group=3)
assert "123 active gates" in summary, summary
assert "reserved gates" not in summary, summary

bad_dir = Path("_smoke_runs/remote_verilog_quality_gates/bad")
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

good_dir = Path("_smoke_runs/remote_verilog_quality_gates/good")
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
  --out _smoke_runs/remote_eval_skill.json \
  --no-state
__PY__ - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("_smoke_runs/remote_eval_skill.json").read_text(encoding="utf-8"))
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
import shutil
from pathlib import Path

for rel in ("_smoke_runs",):
    path = Path(rel)
    if path.exists():
        if not path.is_dir():
            raise SystemExit(f"Refusing to remove non-directory output path: {{path}}")
        shutil.rmtree(path)
state = Path("workflow-state.json")
if state.exists():
    if not state.is_file():
        raise SystemExit(f"Refusing to remove non-file workflow state path: {{state}}")
    state.unlink()
PY"""

    # 默认保留远端输出，方便用户复查 retained run。
    return "echo 'remote_outputs_retained=_smoke_runs workflow-state.json'"

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
        f"echo 'simulator_backend_selection={str_selected_backend}'"
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
