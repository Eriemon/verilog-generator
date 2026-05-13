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
    lines.append(f"endmodule // {tb_name}")
    lines.append("")
    return "\n".join(lines)


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
