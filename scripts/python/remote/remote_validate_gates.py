"""生成远端文件名门禁和合法 testbench 仿真 shell 片段。"""

# _sh_quote 保护嵌入 shell 的解释器命令。
def _sh_quote(str_value: str) -> str:
    """用 POSIX 单引号保护嵌入 shell 的解释器命令。

    :param str_value: 需要嵌入远端 shell 的解释器命令文本。
    :return: 已完成单引号转义的 shell 参数文本。
    """

    # 单引号参数内的单引号必须拆分后重新拼接，避免改变 heredoc 命令边界。
    return "'" + str_value.replace("'", "'\"'\"'") + "'"

# filename_gate_remote_snippet 生成 VG148/VG149 远端交付门与 xsim 准入片段。
def filename_gate_remote_snippet(str_remote_python: str) -> str:
    """生成远端文件名门禁与合法 testbench 仿真片段。

    :param str_remote_python: 远端 Python 命令。
    :return: 可嵌入主 bash 脚本的文件名回归片段。
    """

    # 文件名 probe 的解释器名称先做 shell quoting，避免破坏 heredoc 前缀。
    str_py = _sh_quote(str_remote_python)  # 文件名门禁片段使用的 Python 命令

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
