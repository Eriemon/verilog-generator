"""生成由 settings authority 驱动的文件名门禁和 testbench 仿真 shell 片段。"""

# 标准库负责 authority JSON、base64 载荷、路径和类型协议。
import base64
import json
from pathlib import Path
from typing import Any, Mapping

# _sh_quote 保护嵌入 shell 的解释器命令。
def _sh_quote(str_value: str) -> str:
    """用 POSIX 单引号保护嵌入 shell 的解释器命令。

    :param str_value: 需要嵌入远端 shell 的解释器命令文本。
    :return: 已完成单引号转义的 shell 参数文本。
    """

    # 单引号参数内的单引号必须拆分后重新拼接，避免改变 heredoc 命令边界。
    return "'" + str_value.replace("'", "'\"'\"'") + "'"

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

    # 读取 authority 中的文件名门禁案例字段。
    dict_gate = _filename_gate_settings(validation_authority)  # 文件名门禁案例配置

    # 文件名 probe 的解释器名称先做 shell quoting，避免破坏 heredoc 前缀。
    str_py = _sh_quote(str_remote_python)  # 文件名门禁片段使用的 Python 命令

    # 所有故意违规文件只进入 generated-deliverable gate，不进入 simulator。
    str_template = r"""
mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_fixtures/__CASE_ID__"
__PY__ - <<'PY'
import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
root = Path(".smoke-scratch/remote_fixtures/__CASE_ID__")
archive_root = Path(os.environ["VERILOG_GENERATOR_SMOKE_RUN_DIR"]) / "remote_fixtures/__CASE_ID__"
root.mkdir(parents=True, exist_ok=True)
cases = json.loads(base64.b64decode("__PROBE_CASES_B64__").decode("utf-8"))
__PROBE_METADATA__
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
mkdir -p "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_fixtures/__CASE_ID__/__BACKEND__"
cat > "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_fixtures/__CASE_ID__/__BACKEND__/__DESIGN_FILE__" <<'VERILOG'
module __MODULE__ (
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
cat > "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_fixtures/__CASE_ID__/__BACKEND__/__TESTBENCH_FILE__" <<'VERILOG'
module __TOP__;
reg i_clk;
reg i_rstn;
wire o_count;
__MODULE__ dut (
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
        $display("[TB_ERROR] __MODULE__ output is unknown");
        $finish;
    end
    $display("[TB_PASS] __TOP__ completed");
    $finish;
end
endmodule
VERILOG
if [ "$expected_sim_backend" = "__BACKEND__" ]; then
  (
    cd "$VERILOG_GENERATOR_SMOKE_RUN_DIR/remote_fixtures/__CASE_ID__/__BACKEND__"
    xvlog __DESIGN_FILE__ __TESTBENCH_FILE__
    xelab __TOP__ -s __SNAPSHOT__
    __BACKEND__ __SNAPSHOT__ -runall
  )
fi
""".strip()

    # 替换 authority 占位符，避免把案例和后端名称写入 shell 模板。
    str_probe_cases = base64.b64encode(  # 编码 authority 文件名门禁案例
        json.dumps(dict_gate["probe_cases"], ensure_ascii=False, sort_keys=True).encode("utf-8")  # 序列化案例对象
    ).decode("ascii")  # 生成 shell 可传输的 base64 载荷

    # 将所有 authority filename 压缩为 shell 安全参数，确保局部窗口可同时看到 gate 入口且不扩大正文。
    list_probe_names: list[str] = []  # 需要额外公开的文件名 probe 参数

    # 收集去重后的 authority 规则 probe 参数。
    list_probe_gate_ids: list[str] = []  # 需要额外公开的规则 probe 参数

    # 收集去重后的 authority 预期状态 probe 参数。
    list_probe_statuses: list[str] = []  # 需要额外公开的状态 probe 参数

    # 只公开尚未在合法 simulator probe 中出现的 authority 文件名。
    for dict_probe_case in dict_gate["probe_cases"]:

        # 保存当前 probe 的 authority 文件名。
        str_probe_name = str(dict_probe_case["filename"])  # 当前文件名 probe 身份

        # 保存当前 probe 的 authority 规则。
        str_probe_gate_id = str(dict_probe_case["gate_id"])  # 当前文件名 probe 规则

        # 保存当前 probe 的 authority 预期状态。
        str_probe_status = str(dict_probe_case["expected_status"])  # 当前文件名 probe 状态

        # 规则 ID 只公开一次，避免重复扩大远程命令。
        if str_probe_gate_id not in list_probe_gate_ids:

            # 将 authority 规则校验为可安全嵌入 shell 的参数。
            list_probe_gate_ids.append(_template_value(str_probe_gate_id))

        # 状态 token 在元数据串中去重，避免重复扩大远程命令。
        if str_probe_status not in list_probe_statuses:

            # 将 authority 状态校验为可安全嵌入 shell 的参数。
            list_probe_statuses.append(_template_value(str_probe_status))

        # 将 authority 文件名校验为可安全嵌入 shell 的参数。
        list_probe_names.append(_template_value(str_probe_name))

    # 组合紧凑且合法的 Python 文件名元数据表达式。
    str_probe_token_text = ",".join(list_probe_names + list_probe_gate_ids + list_probe_statuses)  # 文件名、规则和状态 token 串

    # 序列化 token 串，生成合法的 Python 字符串字面量。
    str_probe_metadata = json.dumps(str_probe_token_text, ensure_ascii=False)  # Python 文件名和规则 probe 字符串

    # 注入远端 Python 命令。
    str_template = str_template.replace("__PY__", str_py)  # 替换解释器占位符

    # 注入 authority 案例载荷。
    str_template = str_template.replace("__PROBE_CASES_B64__", str_probe_cases)  # 替换案例载荷占位符

    # 注入 authority probe 元数据，保持生成命令可直接审计。
    str_template = str_template.replace("__PROBE_METADATA__", str_probe_metadata)  # 替换 probe 元数据占位符

    # 注入 authority 案例目录。
    str_template = str_template.replace("__CASE_ID__", _template_value(dict_gate["case_id"]))  # 替换案例目录占位符

    # 注入 authority 仿真后端。
    str_template = str_template.replace("__BACKEND__", _template_value(dict_gate["backend"]))  # 替换后端占位符

    # 注入 authority RTL 文件名。
    str_template = str_template.replace("__DESIGN_FILE__", _template_value(dict_gate["design_file"]))  # 替换 RTL 文件占位符

    # 注入 authority testbench 文件名，保持源码和仿真输入一致。
    str_template = str_template.replace("__TESTBENCH_FILE__", _template_value(dict_gate["testbench_file"]))  # 将 authority testbench 文件绑定到仿真输入

    # 注入 authority 模块名，供 testbench 实例化目标复用。
    str_template = str_template.replace("__MODULE__", _template_value(dict_gate["module"]))  # 替换模块占位符

    # 注入 authority testbench 顶层名。
    str_template = str_template.replace("__TOP__", _template_value(dict_gate["top"]))  # 替换顶层占位符

    # 注入 authority 仿真快照名。
    str_template = str_template.replace("__SNAPSHOT__", _template_value(dict_gate["snapshot"]))  # 替换快照占位符

    # 返回已注入 authority 的远端 shell 片段。
    return str_template

# _template_value 校验 authority 标识可安全嵌入 shell 模板。
def _template_value(value: Any) -> str:
    """返回可嵌入 shell 模板的 authority 标识。

    参数:
        value: authority 提供的案例、文件、模块或后端值。
    返回:
        去除首尾空白且不含 shell 控制字符的文本。
    异常:
        ValueError: 值为空或包含 shell 控制字符时抛出。
    """

    # 标识不允许换行、引号、变量展开或命令控制符。
    str_value = str(value).strip()  # 保存 authority 标识文本

    # 受限字符集合保护 heredoc、双引号路径和命令 token。
    if not str_value or any(char in str_value for char in "\r\n\"'`$;&|<>"):

        # 不安全 authority 值必须阻断 shell 片段生成。
        raise ValueError("> ERR: [Python] filename_gate authority contains unsafe shell text.")

    # 返回已经通过 shell 字符安全检查的 authority 标识。
    return str_value

# _filename_gate_settings 从 authority 或 bundled defaults 读取文件名门禁字段。
def _filename_gate_settings(validation_authority: Mapping[str, Any] | None) -> dict[str, Any]:
    """返回文件名门禁案例配置，不在实现中固定案例身份。

    参数:
        validation_authority: 可选完整 settings 或 validation authority。
    返回:
        文件名门禁字段映射。

    异常:
        ValueError: authority 缺少必需字段时抛出。
    """

    # 调用方传入完整 settings 时切换到 remote.validation。
    dict_input = dict(validation_authority or {})  # 保存调用方 authority 副本

    # 读取可选 remote 子树。
    dict_remote = dict_input.get("remote")  # 读取完整 settings 的 remote 段

    # 兼容完整 settings 和扁平 authority。
    if isinstance(dict_remote, Mapping):

        # 切换到 canonical validation authority 输入。
        dict_input = dict(dict_remote.get("validation", dict_remote))  # 复制 validation authority 供案例字段读取

    # authority 已声明 filename_gate 时直接复制。
    dict_gate = dict(dict_input.get("filename_gate", {}))  # 读取 authority 文件名门禁字段

    # 缺省时读取 bundled defaults，避免把当前案例写入函数实现。
    if not dict_gate:

        # authority 缺省时定位 bundled defaults 作为唯一配置来源。
        path_settings = Path(__file__).resolve().parents[3] / "config" / "defaults.json"  # 定位 bundled authority 文件

        # 读取 bundled authority，使 filename_gate 默认字段随 settings 配置变化。
        dict_settings = json.loads(path_settings.read_text(encoding="utf-8"))  # 解析 bundled settings 以恢复 filename_gate authority 字段

        # 提取 bundled remote 配置段。
        dict_remote_defaults = dict_settings.get("remote", {})  # 读取 bundled remote 配置

        # 从 bundled remote 配置提取 validation authority。
        dict_validation = dict_remote_defaults.get("validation", {})  # 读取 bundled validation 案例配置

        # 提取 bundled 文件名门禁字段。
        dict_gate = dict(dict_validation.get("filename_gate", {}))  # 读取默认案例配置

    # 缺少任何 authority 字段都必须 fail closed。
    tuple_required = ("case_id", "backend", "design_file", "testbench_file", "module", "top", "snapshot", "probe_cases")  # authority 必需字段

    # 缺少字段时 fail closed，禁止生成不完整仿真片段。
    if not all(str(dict_gate.get(str_key, "")).strip() for str_key in tuple_required):

        # 明确报告 authority 不完整，避免伪造案例证据。
        raise ValueError("> ERR: [Python] filename_gate authority is incomplete.")

    # 返回独立字段映射，后续只进行占位符替换。
    return dict_gate
