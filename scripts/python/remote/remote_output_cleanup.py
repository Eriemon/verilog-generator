"""生成远端 smoke 输出保留和清理策略的 shell 片段。"""

# build_remote_output_cleanup_snippet 只生成受控 retained reports 边界检查。
def build_remote_output_cleanup_snippet(
    bool_cleanup_outputs: bool,
    str_remote_python_quoted: str,
) -> str:
    """构造远端 smoke 输出保留或清理策略片段。

    :param bool_cleanup_outputs: 是否进入受控 reports 边界检查分支。
    :param str_remote_python_quoted: 已完成 shell quoting 的远端 Python 命令。
    :return: 可嵌入远端 bash gate 的输出处置片段。
    """

    # 新运行目录是审计归档的一部分，默认策略始终保留 reports 和失败证据。
    if bool_cleanup_outputs:

        # 归档校验只接受固定的 runs/validation_*/reports 层级。
        return f"""{str_remote_python_quoted} - <<'PY'
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
