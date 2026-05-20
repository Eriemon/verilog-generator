"""Shared helpers for Verilog smoke gates."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def temporary_cwd(path: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def remove_tree_with_retry(path: Path, *, attempts: int = 5, delay_s: float = 0.1, ignore_errors: bool = False) -> None:
    last_error: OSError | None = None
    for _ in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            last_error = exc
            if not path.exists():
                return
            time.sleep(delay_s)
    if ignore_errors:
        return
    if last_error is not None:
        raise last_error


def write_fake_skill(path: Path) -> None:
    (path / "agents").mkdir(parents=True, exist_ok=True)
    (path / "SKILL.md").write_text(
        "---\nname: fake\ndescription: fake\n---\n",
        encoding="utf-8",
    )
    (path / "agents" / "openai.yaml").write_text("display_name: Fake\n", encoding="utf-8")


@contextmanager
def fake_tool_path(base: Path, case_name: str, tools: tuple[str, ...]):
    tool_dir = base / "fake-tools" / case_name
    tool_dir.mkdir(parents=True, exist_ok=True)
    log_path = tool_dir / "tool-calls.log"
    for tool in tools:
        write_fake_tool(tool_dir, tool)
    old_path = os.environ.get("PATH", "")
    old_tool_log = os.environ.get("TOOL_LOG")
    os.environ["PATH"] = str(tool_dir)
    os.environ["TOOL_LOG"] = str(log_path)
    try:
        yield log_path
    finally:
        os.environ["PATH"] = old_path
        if old_tool_log is None:
            os.environ.pop("TOOL_LOG", None)
        else:
            os.environ["TOOL_LOG"] = old_tool_log


def write_fake_tool(tool_dir: Path, tool: str) -> None:
    if os.name == "nt":
        script = tool_dir / f"{tool}.cmd"
        script.write_text(f"@echo off\r\n>>\"%TOOL_LOG%\" echo {tool} %*\r\nexit /b 0\r\n", encoding="utf-8")
        return
    script = tool_dir / tool
    script.write_text(
        f"#!/bin/sh\nprintf '%s' '{tool}' >> \"$TOOL_LOG\"\nfor arg in \"$@\"; do printf ' %s' \"$arg\" >> \"$TOOL_LOG\"; done\nprintf '\\n' >> \"$TOOL_LOG\"\nexit 0\n",
        encoding="utf-8",
    )
    script.chmod(0o755)


def tool_calls(log_path: Path) -> list[str]:
    if not log_path.exists():
        return []
    calls: list[str] = []
    for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.strip().split()
        if parts:
            calls.append(parts[0])
    return calls


def rtl_smoke_spec() -> dict:
    return {
        "name": "erie_adapter",
        "target": "rtl",
        "rtl_style_profile": "erie_strict",
        "design_requirements": {
            "target": "rtl",
            "pipeline_required": True,
            "streamability": "non_streamable",
            "interface_family": "native",
            "interface_profile": {},
            "confirmed_by_user": True,
            "confirmation_notes": "Use the native RTL port set with a pipelined Erie-style implementation.",
        },
        "streamability": "non_streamable",
        "interface_family": "native",
        "interface_profile": {},
        "pipeline_required": True,
        "codegen_plan_required": True,
        "description": "Strict Erie RTL smoke example.",
        "interfaces": {
            "ports": [
                {"name": "i_clk", "direction": "input", "width": 1, "role": "clock"},
                {"name": "i_rstn", "direction": "input", "width": 1, "role": "reset"},
                {"name": "i_in_valid", "direction": "input", "width": 1},
                {"name": "i_in_data", "direction": "input", "width": 8},
                {"name": "o_out_valid", "direction": "output", "width": 1},
                {"name": "o_out_data", "direction": "output", "width": 8},
            ]
        },
        "behavior": ["Forward the input data and valid signal by one cycle."],
        "clock": {"name": "i_clk", "edge": "posedge", "frequency_mhz": 100},
        "reset": {"name": "i_rstn", "active": "low", "synchronous": False},
        "constraints": ["Use synthesizable Verilog-2001."],
        "outputs": [
            {"path": "rtl/erie_adapter.v", "kind": "source", "language": "verilog"},
            {"path": "tb/erie_adapter_tb.v", "kind": "testbench", "language": "verilog"},
        ],
        "notes": [],
        "subfunctions": [],
        "workflow": {},
        "performance": {},
    }


def interface_policy_spec(description: str, *, clock: str = "i_clk", reset: str = "i_rstn") -> dict:
    return {
        "name": "interface_policy_smoke",
        "target": "rtl",
        "rtl_dialect": "verilog",
        "pipeline_required": True,
        "codegen_plan_required": True,
        "description": description,
        "interfaces": {
            "ports": [
                {"name": clock, "direction": "input", "width": 1, "role": "clock"},
                {"name": reset, "direction": "input", "width": 1, "role": "reset"},
                {"name": "i_data", "direction": "input", "width": 32},
                {"name": "o_data", "direction": "output", "width": 32},
            ]
        },
        "behavior": [description],
        "clock": {"name": clock, "edge": "posedge", "frequency_mhz": 100},
        "reset": {"name": reset, "active": "low", "synchronous": False},
        "constraints": ["Use synthesizable Verilog-2001."],
        "outputs": [
            {"path": "rtl/interface_policy_smoke.v", "kind": "source", "language": "verilog"},
            {"path": "tb/interface_policy_smoke_tb.v", "kind": "testbench", "language": "verilog"},
        ],
        "notes": [],
        "subfunctions": [],
        "workflow": {},
        "performance": {},
    }


def write_mock_rtl_artifacts(spec: dict, generated_dir: Path) -> Path:
    generated_dir.mkdir(parents=True, exist_ok=True)
    outputs = spec["outputs"]
    source_path = generated_dir / outputs[0]["path"]
    tb_path = generated_dir / outputs[1]["path"]
    source_path.parent.mkdir(parents=True, exist_ok=True)
    tb_path.parent.mkdir(parents=True, exist_ok=True)
    module_name = spec["name"]
    source_text = (
        "module "
        + module_name
        + "(\n"
        + "\tinput i_clk,\n"
        + "\tinput i_rstn,\n"
        + "\tinput i_in_valid,\n"
        + "\tinput [7:0] i_in_data,\n"
        + "\toutput o_out_valid,\n"
        + "\toutput [7:0] o_out_data\n"
        + ");\n"
        + "\treg state_current;\n"
        + "\treg state_next;\n"
        + "\treg [7:0] reg_data;\n"
        + "\treg reg_valid;\n"
        + "\tlocalparam ST_IDLE = 1'b0;\n"
        + "\tlocalparam ST_RUN = 1'b1;\n"
        + "\tassign o_out_valid = reg_valid;\n"
        + "\tassign o_out_data = reg_data;\n"
        + "\talways @(posedge i_clk or negedge i_rstn) begin\n"
        + "\t\tif (!i_rstn) begin\n"
        + "\t\t\tstate_current <= ST_IDLE;\n"
        + "\t\t\treg_data <= 8'd0;\n"
        + "\t\t\treg_valid <= 1'b0;\n"
        + "\t\tend else begin\n"
        + "\t\t\tstate_current <= state_next;\n"
        + "\t\t\treg_data <= i_in_data;\n"
        + "\t\t\treg_valid <= i_in_valid;\n"
        + "\t\tend\n"
        + "\tend\n"
        + "\talways @(*) begin\n"
        + "\t\tstate_next = state_current;\n"
        + "\t\tcase (state_current)\n"
        + "\t\t\tST_IDLE: if (i_in_valid) state_next = ST_RUN;\n"
        + "\t\t\tST_RUN: state_next = ST_RUN;\n"
        + "\t\t\tdefault: state_next = ST_IDLE;\n"
        + "\t\tendcase\n"
        + "\tend\n"
        + "endmodule\n"
    )
    source_path.write_text(_add_verilog_line_comments(source_text), encoding="utf-8")
    tb_text = (
        "module "
        + module_name
        + "_tb;\n"
        + "\treg i_clk;\n"
        + "\treg i_rstn;\n"
        + "\treg i_in_valid;\n"
        + "\treg [7:0] i_in_data;\n"
        + "\twire o_out_valid;\n"
        + "\twire [7:0] o_out_data;\n"
        + "\t"
        + module_name
        + " DUT_Inst(\n"
        + "\t\t.i_clk(i_clk),\n"
        + "\t\t.i_rstn(i_rstn),\n"
        + "\t\t.i_in_valid(i_in_valid),\n"
        + "\t\t.i_in_data(i_in_data),\n"
        + "\t\t.o_out_valid(o_out_valid),\n"
        + "\t\t.o_out_data(o_out_data)\n"
        + "\t);\n"
        + "\tinitial begin\n"
        + "\t\ti_clk = 1'b0;\n"
        + "\t\tforever #5 i_clk = ~i_clk;\n"
        + "\tend\n"
        + "\tinitial begin\n"
        + "\t\ti_rstn = 1'b0;\n"
        + "\t\ti_in_valid = 1'b0;\n"
        + "\t\ti_in_data = 8'd0;\n"
        + "\t\t#20;\n"
        + "\t\ti_rstn = 1'b1;\n"
        + "\t\t#10;\n"
        + "\t\ti_in_valid = 1'b1;\n"
        + "\t\ti_in_data = 8'hA5;\n"
        + "\t\t#10;\n"
        + "\t\tif (o_out_data !== 8'hA5) begin\n"
        + "\t\t\t$display(\"FAIL\");\n"
        + "\t\t\t$finish_and_return(1);\n"
        + "\t\tend\n"
        + "\t\t$display(\"PASS\");\n"
        + "\t\t$finish;\n"
        + "\tend\n"
        + "endmodule\n"
    )
    tb_path.write_text(_add_verilog_line_comments(tb_text), encoding="utf-8")
    return generated_dir


def _add_verilog_line_comments(text: str) -> str:
    rendered_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("//") and "//" not in line:
            rendered_lines.append(f"{line} //逐行中文注释")
        else:
            rendered_lines.append(line)
    return "\n".join(rendered_lines) + "\n"
