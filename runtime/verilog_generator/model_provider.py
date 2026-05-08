"""Pluggable model-provider adapters for workflow execution."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from .vectors import VECTOR_HASH_TAG


class ModelProviderError(ValueError):
    """Raised when a model provider cannot return a valid response."""


class ManualResponseRequired(ModelProviderError):
    """Raised when the manual provider has no prepared response file."""


@dataclass(frozen=True)
class GenerationContext:
    """Stable provider context for one generation stage."""

    attempt_id: str
    stage: str
    prompt_path: Path
    response_path: Path
    run_dir: Path
    attempt_dir: Path
    spec: dict[str, Any]
    manifest: dict[str, Any]
    workflow_config: dict[str, Any]
    vector_contract: dict[str, Any] | None = None
    comment_language: str = "zh"


class ModelProvider(Protocol):
    """Simple response-generation contract used by the workflow runner."""

    name: str

    def generate(self, prompt: str, context: GenerationContext) -> str:
        """Return a raw fenced-block model response."""


def build_model_provider(
    provider_name: str,
    *,
    command: str | Sequence[str] | None = None,
    timeout_s: int = 120,
    config: dict[str, Any] | None = None,
) -> ModelProvider:
    normalized = provider_name.lower()
    if normalized == "mock":
        return MockModelProvider(config=config)
    if normalized == "manual":
        return ManualModelProvider()
    if normalized == "command":
        if not command:
            raise ModelProviderError("Command provider requires a model command.")
        return CommandModelProvider(command, timeout_s=timeout_s)
    raise ModelProviderError(f"Unknown model provider {provider_name!r}.")


class ManualModelProvider:
    name = "manual"

    def generate(self, prompt: str, context: GenerationContext) -> str:
        del prompt
        if not context.response_path.exists():
            raise ManualResponseRequired(f"Manual provider expects a prepared response file at {context.response_path}.")
        return context.response_path.read_text(encoding="utf-8")


class CommandModelProvider:
    name = "command"

    def __init__(self, command: str | Sequence[str], *, timeout_s: int = 120) -> None:
        self._command = _normalize_command(command)
        self._timeout_s = timeout_s

    def generate(self, prompt: str, context: GenerationContext) -> str:
        env = os.environ.copy()
        env.update(
            {
                "VERILOG_GEN_PROMPT_PATH": str(context.prompt_path),
                "VERILOG_GEN_RESPONSE_PATH": str(context.response_path),
                "VERILOG_GEN_STAGE": context.stage,
                "VERILOG_GEN_ATTEMPT_ID": context.attempt_id,
                "VERILOG_GEN_CONTEXT_JSON": json.dumps(
                    {
                        "attempt_id": context.attempt_id,
                        "stage": context.stage,
                        "prompt_path": str(context.prompt_path),
                        "response_path": str(context.response_path),
                        "run_dir": str(context.run_dir),
                        "attempt_dir": str(context.attempt_dir),
                        "target": "rtl",
                        "name": context.spec.get("name"),
                        "manifest": context.manifest,
                    },
                    ensure_ascii=False,
                ),
            }
        )
        command = [_expand_part(part, context) for part in self._command]
        try:
            result = subprocess.run(
                command,
                cwd=context.run_dir,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self._timeout_s,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ModelProviderError(f"Command provider timed out after {self._timeout_s}s.") from exc
        except OSError as exc:
            raise ModelProviderError(f"Command provider failed to start: {exc}") from exc
        if result.returncode != 0:
            output = (result.stderr or result.stdout).strip()
            detail = output.splitlines()[0] if output else f"exit code {result.returncode}"
            raise ModelProviderError(f"Command provider failed: {detail}")
        if result.stdout.strip():
            return result.stdout
        if context.response_path.exists():
            return context.response_path.read_text(encoding="utf-8")
        raise ModelProviderError("Command provider produced no stdout and did not write the expected response file.")


class MockModelProvider:
    name = "mock"

    def __init__(self, *, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}

    def generate(self, prompt: str, context: GenerationContext) -> str:
        del prompt
        mode = _mock_mode(context, self._config)
        if mode == "invalid_response":
            return "This is not a fenced response.\n"

        manifest = context.manifest
        files = [entry for entry in manifest.get("files", []) if isinstance(entry, dict) and entry.get("path")]
        if mode == "spec_issue" and len(files) > 1:
            dropped_path = next(
                (str(entry["path"]) for entry in files if entry.get("kind") == "testbench" or "_tb." in str(entry["path"]).lower()),
                str(files[-1]["path"]),
            )
            files = [entry for entry in files if str(entry["path"]) != dropped_path]
        response_manifest = {
            **manifest,
            "files": files,
            "checks": {
                "spec_coverage": [f"Mock provider generated stage {context.stage} artifacts."],
                "verification_plan": ["Mock response includes deterministic verification hooks."],
                "execution_plan": ["Mock response is intended for local workflow tests."],
                "implementation_assessment": ["Mock artifacts satisfy structural contracts for the workflow runner."],
                "reviewability_assessment": ["Mock artifacts keep comments and markers for validation."],
                "assumptions": [],
                "known_limitations": ["Mock provider prioritizes workflow determinism over hardware fidelity."],
            },
        }
        blocks = ["```json", json.dumps(response_manifest, indent=2, ensure_ascii=False), "```"]
        file_map = _mock_file_contents(context, files)
        for file_entry in files:
            rel_path = str(file_entry["path"])
            language = str(file_entry.get("language") or "text")
            blocks.extend([f"```{language} path={rel_path}", file_map[rel_path].rstrip(), "```"])
        return "\n".join(blocks) + "\n"


def _normalize_command(command: str | Sequence[str]) -> list[str]:
    if isinstance(command, str):
        parts = shlex.split(command, posix=False)
    else:
        parts = [str(item) for item in command]
    if not parts:
        raise ModelProviderError("Model command must not be empty.")
    return parts


def _expand_part(part: str, context: GenerationContext) -> str:
    values = {
        "attempt_id": context.attempt_id,
        "stage": context.stage,
        "prompt_path": str(context.prompt_path),
        "response_path": str(context.response_path),
        "run_dir": str(context.run_dir),
        "attempt_dir": str(context.attempt_dir),
        "target": "rtl",
        "name": str(context.spec.get("name") or ""),
    }
    try:
        return part.format_map(values)
    except Exception:
        return part


def _mock_mode(context: GenerationContext, config: dict[str, Any]) -> str:
    behavior = config.get("mock_behavior")
    if behavior is None:
        behavior = (context.spec.get("workflow") or {}).get("mock_behavior")
    if isinstance(behavior, str):
        return behavior
    if isinstance(behavior, dict):
        raw = behavior.get(context.stage, behavior.get("*", behavior.get("default", "success")))
        if isinstance(raw, dict):
            return str(raw.get("mode", "success"))
        if raw:
            return str(raw)
    return "success"


def _mock_file_contents(context: GenerationContext, files: list[dict[str, Any]]) -> dict[str, str]:
    stage = context.stage
    spec = context.spec
    vectors = _mock_vectors(spec)
    vector_hash = str((context.vector_contract or {}).get("sha256") or "")
    contents: dict[str, str] = {}
    if stage == "python":
        for file_entry in files:
            rel_path = str(file_entry["path"])
            suffix = Path(rel_path).suffix.lower()
            if suffix == ".py":
                contents[rel_path] = _mock_python_model_text(vectors)
            elif suffix == ".json":
                contents[rel_path] = json.dumps({"cases": vectors}, indent=2, ensure_ascii=False) + "\n"
            else:
                contents[rel_path] = "\n"
        return contents
    if stage == "rtl":
        for file_entry in files:
            rel_path = str(file_entry["path"])
            if Path(rel_path).suffix.lower() == ".v" and "_tb" not in Path(rel_path).stem.lower():
                contents[rel_path] = _mock_erie_rtl_source_text(spec)
            elif Path(rel_path).suffix.lower() == ".v":
                contents[rel_path] = _mock_erie_rtl_testbench_text(spec, vectors, vector_hash)
            else:
                contents[rel_path] = "\n"
        return contents
    if stage == "tests":
        payload = {"version": 1, "cases": vectors}
        for file_entry in files:
            contents[str(file_entry["path"])] = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        return contents
    for file_entry in files:
        contents[str(file_entry["path"])] = "{}\n"
    return contents


def _mock_vectors(spec: dict[str, Any]) -> list[dict[str, Any]]:
    configured = (spec.get("workflow") or {}).get("mock_vectors")
    if isinstance(configured, list) and configured:
        return configured
    return [
        {
            "id": "case_1",
            "inputs": {"value": 1},
            "expected_outputs": {"value": 1},
            "checkpoints": {"value": 1},
        }
    ]


def _mock_python_model_text(vectors: list[dict[str, Any]]) -> str:
    payload = repr(vectors)
    return f"""REFERENCE_VECTORS = {payload}


def run_case(case):
    if "expected_outputs" in case:
        return case["expected_outputs"]
    if "expected" in case:
        return case["expected"]
    if "outputs" in case:
        return case["outputs"]
    inputs = case.get("inputs", {{}})
    if isinstance(inputs, dict):
        return inputs
    return {{"result": inputs}}


def collect_checkpoints(case):
    if "checkpoints" in case:
        return case["checkpoints"]
    return {{"observed": run_case(case)}}


def run_tests():
    for case in REFERENCE_VECTORS:
        expected = case.get("expected_outputs", run_case(case))
        if run_case(case) != expected:
            print(f"FAIL {{case.get('id', 'case')}}")
            return False
    print("PASS")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if run_tests() else 1)
"""


def _mock_erie_rtl_source_text(spec: dict[str, Any]) -> str:
    top = str(spec.get("name") or "rtl_module")
    ports = [item for item in spec.get("interfaces", {}).get("ports", []) if isinstance(item, dict) and item.get("name")]
    clock_name = next((str(item["name"]) for item in ports if item.get("role") == "clock"), "i_clk")
    reset_name = next((str(item["name"]) for item in ports if item.get("role") == "reset"), "i_rstn")
    inputs = [item for item in ports if str(item.get("direction")) == "input" and item.get("role") not in {"clock", "reset"}]
    outputs = [item for item in ports if str(item.get("direction")) == "output"]
    data_input = next((item for item in inputs if "data" in str(item.get("name")).lower()), inputs[0] if inputs else None)
    valid_input = next((item for item in inputs if "valid" in str(item.get("name")).lower()), None)
    data_output = next((item for item in outputs if "data" in str(item.get("name")).lower()), outputs[-1] if outputs else None)
    valid_output = next((item for item in outputs if "valid" in str(item.get("name")).lower()), outputs[0] if outputs else None)
    data_output_internal = _internal_output_name(str(data_output["name"])) if data_output else "data_o"
    valid_output_internal = _internal_output_name(str(valid_output["name"])) if valid_output else "valid_o"
    data_reg = "reg_data_hold"
    valid_reg = "reg_valid_hold"
    port_lines = []
    for index, item in enumerate(ports):
        width = int(item.get("width", 1) or 1)
        width_text = "" if width <= 1 else f"[{width - 1}:0] "
        trailing = "," if index < len(ports) - 1 else ""
        port_lines.append(f"\t{item['direction']} {width_text}{item['name']}{trailing}\t\t\t\t//端口信号注释")
    port_block = "\n".join(port_lines)
    data_reg_width = _width_text(data_output or data_input)
    valid_reg_width = _width_text(valid_output or valid_input)
    data_sample = str(data_input["name"]) if data_input else "i_data"
    valid_sample = str(valid_input["name"]) if valid_input else "i_valid"
    data_width = int((data_output or data_input or {"width": 8}).get("width", 8))
    return f"""`timescale 1ns / 1ps

////////////////////////////////////English///////////////////////////////////////
// Company:\t\t\tErie
// Engineer:\t\tErie
// 
// Create Date:\t2026/05/03 12:00:00
// Design Name:\t{top}
// Module Name:\t{top}
// Description:\tDescription/{top}_Design.pdf
// 
// Version:\t\t\tV1.0
// Revision Date:\t2026/05/03 12:00:00
///////////////////////////////////Chinese////////////////////////////////////////
// 版权归属:\t\tErie
// 开发人员:\t\tErie
// 
// 创建日期:\t\t2026年05月03日
// 设计名称:\t\t{top}
// 模块名称:\t\t{top}
// 当前版本:\t\tV1.0
// 修订日期:\t\t2026年05月03日

//模块说明
module {top}
#(
\tparameter C_DATA_WIDTH = {data_width}\t//数据位宽参数,默认值,参数解释说明中文
)
(
{port_block}
);
\t//---------------配置参数区域---------------//
\tlocalparam DATA_RESET_VALUE = {{C_DATA_WIDTH{{1'b0}}}};\t//数据复位值参数说明

\t//---------------状态参数区域---------------//
\t//此模板未使用状态参数

\t//--------------模块实例化信号区域--------------//
\t//此模板未使用模块实例化信号

\t//-----------------计数信号区域-----------------//
\t//此模板未使用计数信号

\t//----------------状态机信号区域----------------//
\t//此模板未使用状态机信号

\t//----------------寄存器信号区域----------------//
\treg {data_reg_width}{data_reg} = DATA_RESET_VALUE;\t//寄存器功能说明,必须要有的注释
\treg {valid_reg_width}{valid_reg} = 1'b0;\t//寄存器功能说明,必须要有的注释

\t//-----------------标志信号区域-----------------//
\t//此模板未使用标志信号

\t//-----------------编码信号区域-----------------//
\t//此模板未使用编码信号

\t//-----------------译码信号区域-----------------//
\t//此模板未使用译码信号

\t//-----------------其他信号区域-----------------//
\t//此模板未使用其他信号

\t//-----------------输出信号区域-----------------//
\treg {valid_reg_width}{valid_output_internal} = 1'b0;\t//输出信号内部寄存器
\treg {data_reg_width}{data_output_internal} = DATA_RESET_VALUE;\t//输出信号内部寄存器

\t//---------------其他信号连线区域---------------//
\t//此模板未使用其他信号连线

\t//---------------输出信号连线区域---------------//
\tassign {valid_output['name'] if valid_output else 'o_valid'} = {valid_output_internal};\t//输出端口连接
\tassign {data_output['name'] if data_output else 'o_data'} = {data_output_internal};\t//输出端口连接

\t//-------------输出信号处理区域-------------//
\t//输出信号总线--控制通道--输出有效信号功能
\talways@(posedge {clock_name} or negedge {reset_name})begin
\t\tif({reset_name} == 1'b0){valid_output_internal} <= 1'b0;
\t\telse if({valid_reg} == 1'b1){valid_output_internal} <= 1'b1;
\t\telse {valid_output_internal} <= 1'b0;
\tend

\t//输出信号总线--数据通道--输出数据信号功能
\talways@(posedge {clock_name} or negedge {reset_name})begin
\t\tif({reset_name} == 1'b0){data_output_internal} <= DATA_RESET_VALUE;
\t\telse if({valid_reg} == 1'b1){data_output_internal} <= {data_reg};
\t\telse {data_output_internal} <= {data_output_internal};
\tend

\t//----------------状态机区域----------------//
\t//此模板未使用状态机

\t//-------------状态任务处理区域-------------//
\t//此模板未使用状态任务

\t//-------------主要任务处理区域-------------//
\t//寄存器名称,必须要有的注释:缓存输入数据
\talways@(posedge {clock_name} or negedge {reset_name})begin
\t\tif({reset_name} == 1'b0){data_reg} <= DATA_RESET_VALUE;
\t\telse if({valid_sample} == 1'b1){data_reg} <= {data_sample};
\t\telse {data_reg} <= {data_reg};
\tend

\t//寄存器名称,必须要有的注释:缓存输入有效信号
\talways@(posedge {clock_name} or negedge {reset_name})begin
\t\tif({reset_name} == 1'b0){valid_reg} <= 1'b0;
\t\telse {valid_reg} <= {valid_sample};
\tend

\t//--------------模块实例化区域--------------//
\t//此模板未使用模块实例化

endmodule
"""


def _mock_erie_rtl_testbench_text(spec: dict[str, Any], vectors: list[dict[str, Any]], vector_hash: str) -> str:
    top = str(spec.get("name") or "rtl_module")
    top_tb = f"{top}_tb"
    lines = [f"module {top_tb};", "\tinitial begin"]
    if vector_hash:
        lines.append(f"\t\t// {VECTOR_HASH_TAG} {vector_hash}")
    for item in vectors:
        lines.append(f'\t\t// {item["id"]} PASS FAIL')
    lines.append('\t\t$display("PASS");')
    lines.append('\t\t$display("FAIL if any check fails");')
    lines.append("\tend")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def _internal_output_name(port_name: str) -> str:
    if port_name.startswith("o_"):
        return port_name[2:] + "_o"
    return port_name + "_o"


def _width_text(signal: dict[str, Any] | None) -> str:
    width = int((signal or {}).get("width", 1) or 1)
    return "" if width <= 1 else f"[{width - 1}:0] "
