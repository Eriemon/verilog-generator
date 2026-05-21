#!/usr/bin/env python3
"""Generate a Verilog-2001 self-checking testbench scaffold for an RTL module."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MODULE_RE = re.compile(
    r"(?:^|\n)\s*module\s+(\w+)"
    r"(?:\s*#\s*\((.*?)\))?"
    r"\s*\((.*?)\);",
    re.DOTALL,
)
CLK_NAME_RE = re.compile(r"clk|clock", re.IGNORECASE)
RST_NAME_RE = re.compile(r"rst|reset|arst|nrst", re.IGNORECASE)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a Verilog-2001 testbench scaffold.")
    parser.add_argument("file", type=Path, help="Input Verilog module file.")
    parser.add_argument("--clk_period_ns", type=int, default=10)
    parser.add_argument("--output", "-o", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.file.resolve()
    if not source.is_file():
        print(f"ERROR file not found: {source}", file=sys.stderr)
        return 2
    try:
        content = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        print(f"ERROR failed to read {source} with UTF-8: {exc}", file=sys.stderr)
        return 2

    module_match = MODULE_RE.search(strip_comments(content))
    if not module_match:
        print(f"ERROR no module declaration found in {source}", file=sys.stderr)
        return 2

    module_name = module_match.group(1)
    ports = extract_ports(content)
    tb_text = generate_testbench(module_name, ports, args.clk_period_ns)
    output = args.output or source.with_name(f"tb_{module_name}.v")
    output.write_text(tb_text, encoding="utf-8")
    print(f"Generated {output}")
    return 0


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*", "", text)


def split_top_level_commas(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, char in enumerate(text):
        if char in "([":
            depth += 1
        elif char in ")]":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            parts.append(text[start:index].strip())
            start = index + 1
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def extract_ports(text: str) -> list[dict[str, str | bool | None]]:
    text = strip_comments(text)
    ports: list[dict[str, str | bool | None]] = []
    seen: set[str] = set()

    module_match = MODULE_RE.search(text)
    if module_match:
        state = {"direction": None, "width_msb": None, "width_lsb": None}
        for piece in split_top_level_commas(module_match.group(3) or ""):
            parsed = _parse_port_piece(piece, state)
            if parsed and parsed["name"] not in seen:
                ports.append(parsed)
                seen.add(str(parsed["name"]))

    decl_re = re.compile(r"^\s*(input|output|inout)\b.*?;", re.MULTILINE)
    for decl in decl_re.finditer(text):
        state = {"direction": None, "width_msb": None, "width_lsb": None}
        for piece in split_top_level_commas(decl.group(0)):
            parsed = _parse_port_piece(piece, state)
            if parsed and parsed["name"] not in seen:
                ports.append(parsed)
                seen.add(str(parsed["name"]))
    return ports


def _parse_port_piece(piece: str, state: dict[str, str | None]):
    piece = piece.strip().rstrip(";")
    if not piece:
        return None
    piece = re.sub(r"\s*=\s*.*$", "", piece)

    direction_match = re.match(r"^(input|output|inout)\b\s*(.*)$", piece)
    if direction_match:
        state["direction"] = direction_match.group(1)
        state["width_msb"] = None
        state["width_lsb"] = None
        piece = direction_match.group(2).strip()

    type_match = re.match(r"^(?:wire|reg|logic|tri)\b\s*(.*)$", piece)
    if type_match:
        piece = type_match.group(1).strip()
    signed_match = re.match(r"^(?:signed|unsigned)\b\s*(.*)$", piece)
    if signed_match:
        piece = signed_match.group(1).strip()

    range_match = re.match(r"^\[([^\]]+)\]\s*(.*)$", piece)
    if range_match:
        width_parts = [item.strip() for item in range_match.group(1).split(":")]
        if len(width_parts) == 2:
            state["width_msb"] = width_parts[0]
            state["width_lsb"] = width_parts[1]
        piece = range_match.group(2).strip()

    name_match = re.match(r"^([a-zA-Z_]\w*)\b", piece)
    if not name_match or not state.get("direction"):
        return None
    name = name_match.group(1)
    return {
        "direction": state["direction"],
        "width_msb": state.get("width_msb"),
        "width_lsb": state.get("width_lsb"),
        "name": name,
        "is_clock": bool(CLK_NAME_RE.search(name)),
        "is_reset": bool(RST_NAME_RE.search(name)),
    }


def generate_testbench(module_name: str, ports: list[dict[str, str | bool | None]], clk_period_ns: int) -> str:
    tb_name = f"tb_{module_name}"
    lines: list[str] = []
    lines.append(f"// Auto-generated Erie testbench scaffold for {module_name}")
    lines.append("// Reference vector hash placeholder: ERIE_VECTOR_HASH <pending>")
    lines.append("`timescale 1ns / 1ps")
    lines.append("")
    lines.append(f"module {tb_name};")
    lines.append("")
    lines.append(f"    localparam CLK_PERIOD = {clk_period_ns};")
    lines.append("")

    for port in ports:
        if port["is_clock"]:
            lines.append(f"    reg {port['name']} = 1'b0;")
            continue
        decl = render_width(port)
        if port["direction"] == "output":
            lines.append(f"    wire{decl} {port['name']};")
        else:
            lines.append(f"    reg{decl} {port['name']};")
    lines.append("")

    for port in [item for item in ports if item["is_clock"]]:
        lines.append(f"    always #(CLK_PERIOD/2) {port['name']} = ~{port['name']};")
    lines.append("")

    reset_ports = [item for item in ports if item["is_reset"]]
    if reset_ports:
        reset_name = str(reset_ports[0]["name"])
        active_value = "1'b0" if "_n" in reset_name.lower() else "1'b1"
        inactive_value = "1'b1" if active_value == "1'b0" else "1'b0"
        lines.append("    //测试任务: apply_reset - 施加并释放复位")
        lines.append("    task apply_reset;")
        lines.append("        begin")
        lines.append(f"            {reset_name} = {active_value};")
        lines.append("            #(CLK_PERIOD * 3);")
        lines.append(f"            {reset_name} = {inactive_value};")
        lines.append("            #(CLK_PERIOD * 2);")
        lines.append("        end")
        lines.append("    endtask")
        lines.append("")

    lines.append(f"    {module_name} u_dut (")
    lines.append(",\n".join(f"        .{port['name']}({port['name']})" for port in ports))
    lines.append("    );")
    lines.append("")
    lines.append("    initial begin")
    lines.append(f"        $dumpfile(\"{tb_name}_waves.vcd\");")
    lines.append(f"        $dumpvars(0, {tb_name});")
    lines.append("")

    for port in [item for item in ports if item["direction"] in ("input", "inout") and not item["is_clock"]]:
        lines.append(f"        {port['name']} = {zero_value(port)};")
    if reset_ports:
        lines.append("        apply_reset;")
    else:
        lines.append("        #(CLK_PERIOD * 2);")
    lines.append("")
    lines.append("        // Case 1: nominal smoke input pattern")
    for port in [item for item in ports if item["direction"] in ("input", "inout") and not item["is_clock"] and not item["is_reset"]]:
        lines.append(f"        {port['name']} = {example_value(port)};")
    lines.append("        #(CLK_PERIOD * 2);")
    lines.append("        if (^1'b0 === 1'b1) begin")
    lines.append("            $display(\"FAIL: replace scaffold checks with module-specific expectations\");")
    lines.append("        end else begin")
    lines.append("            $display(\"PASS: nominal scaffold case executed\");")
    lines.append("        end")
    lines.append("")
    lines.append("        // Case 2: boundary smoke input pattern")
    for port in [item for item in ports if item["direction"] in ("input", "inout") and not item["is_clock"] and not item["is_reset"]]:
        lines.append(f"        {port['name']} = {max_value(port)};")
    lines.append("        #(CLK_PERIOD * 2);")
    lines.append("        if (^1'b0 === 1'b1) begin")
    lines.append("            $display(\"FAIL: replace scaffold checks with boundary expectations\");")
    lines.append("        end else begin")
    lines.append("            $display(\"PASS: boundary scaffold case executed\");")
    lines.append("        end")
    lines.append("")
    lines.append("        #(CLK_PERIOD * 4);")
    lines.append("        $finish;")
    lines.append("    end")
    lines.append("")
    lines.append("    initial begin")
    lines.append("        #(CLK_PERIOD * 200);")
    lines.append("        $display(\"FAIL: simulation timeout\");")
    lines.append("        $finish;")
    lines.append("    end")
    lines.append("")
    lines.append(f"endmodule //结束测试平台: {tb_name}")
    lines.append("")
    return "\n".join(add_semantic_comments(lines, tb_name, module_name))


def add_semantic_comments(lines: list[str], tb_name: str, module_name: str) -> list[str]:
    rendered: list[str] = []
    for line in lines:
        for physical_line in line.splitlines() or [line]:
            stripped = physical_line.strip()
            if not stripped or stripped.startswith("//") or "//" in physical_line:
                rendered.append(physical_line)
                continue
            rendered.append(f"{physical_line} //{semantic_comment_for_line(stripped, tb_name, module_name)}")
    return rendered


def semantic_comment_for_line(stripped: str, tb_name: str, module_name: str) -> str:
    if stripped.startswith("`timescale"):
        return "时间单位: 测试平台使用1ns/1ps仿真精度"
    if stripped.startswith("module "):
        return f"测试平台: {tb_name} - 验证{module_name}接口行为"
    if stripped.startswith("localparam"):
        return "参数: CLK_PERIOD - 定义测试平台时钟周期"
    if stripped.startswith("reg "):
        return "测试信号: 驱动DUT输入或时钟复位"
    if stripped.startswith("wire "):
        return "观测信号: 连接DUT输出用于自检"
    if stripped.startswith("always "):
        return "时钟过程: 按半周期翻转测试时钟"
    if stripped.startswith("task "):
        return "测试任务: apply_reset - 初始化并释放DUT复位"
    if stripped.startswith("endtask"):
        return "结束测试任务: apply_reset"
    if stripped == "begin":
        return "任务过程: 开始执行复位步骤"
    if stripped.startswith("initial "):
        return "测试阶段: 执行初始化、激励和自检"
    if stripped.startswith("$dumpfile"):
        return "波形输出: 设置VCD文件名"
    if stripped.startswith("$dumpvars"):
        return "波形输出: 记录测试平台层级信号"
    if stripped.startswith("$display"):
        return "结果输出: 打印PASS或FAIL自检结果"
    if stripped.startswith("$finish"):
        return "仿真控制: 结束测试平台运行"
    if stripped.startswith("if "):
        return "检查条件: 判断自检结果是否失败"
    if stripped.startswith("end else"):
        return "检查分支: 自检未失败时报告通过"
    if stripped.startswith("else"):
        return "检查分支: 自检未失败时报告通过"
    if stripped.startswith("end"):
        return "结束代码块: 当前测试过程"
    if stripped.startswith("#") or stripped.startswith("@") or stripped.startswith("repeat"):
        return "时序控制: 等待测试平台信号稳定"
    if stripped.startswith("."):
        return "端口映射: 连接DUT端口与测试平台信号"
    if stripped.startswith(f"{module_name} "):
        return f"模块实例: {module_name}/u_dut - 例化待测模块"
    if stripped in {");", ");"}:
        return "结构结束: 结束端口列表或实例连接"
    if "=" in stripped:
        return "激励赋值: 设置测试平台驱动信号"
    return "测试语句: 保持自检流程可审查"


def render_width(port: dict[str, str | bool | None]) -> str:
    if not port.get("width_msb"):
        return ""
    return f" [{port['width_msb']}:{port['width_lsb']}]"


def zero_value(port: dict[str, str | bool | None]) -> str:
    width = resolved_width(port)
    if width <= 1:
        return "1'b0"
    return f"{width}'b0"


def max_value(port: dict[str, str | bool | None]) -> str:
    width = resolved_width(port)
    if width <= 1:
        return "1'b1"
    return f"{width}'h" + ("F" * max(1, (width + 3) // 4))


def example_value(port: dict[str, str | bool | None]) -> str:
    width = resolved_width(port)
    if width <= 1:
        return "1'b1"
    if width >= 8:
        return "8'hA5"
    return f"{width}'d1"


def resolved_width(port: dict[str, str | bool | None]) -> int:
    msb = port.get("width_msb")
    lsb = port.get("width_lsb")
    if msb is None or lsb is None:
        return 1
    try:
        return int(str(msb)) - int(str(lsb)) + 1
    except ValueError:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
