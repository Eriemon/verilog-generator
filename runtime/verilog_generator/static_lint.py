"""Erie-specific heuristic lint checks for generated Verilog RTL."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class StaticLintIssue:
    severity: str
    message: str
    path: str
    line: int
    source: str = "current_module_issue"
    code: str = "ASIC"

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "source": self.source,
            "code": self.code,
        }


def lint_generated_rtl(spec: dict[str, Any], root: Path) -> list[StaticLintIssue]:
    """Run lightweight ASIC-oriented checks on RTL source files only."""
    clock_names = _clock_names(spec)
    issues: list[StaticLintIssue] = []
    for path in sorted(root.glob("**/*.v")):
        if _is_testbench(path):
            continue
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        stripped_lines = [_strip_line_comments(line) for line in _strip_block_comments(text).splitlines()]
        widths = _declared_widths(stripped_lines)
        constants = _constant_names(stripped_lines)
        issues.extend(_function_task_issues(rel, stripped_lines))
        issues.extend(_case_default_issues(rel, stripped_lines))
        issues.extend(_case_default_xz_issues(rel, stripped_lines))
        issues.extend(_casex_casez_issues(rel, stripped_lines))
        issues.extend(_legacy_sensitivity_issues(rel, stripped_lines))
        issues.extend(_sensitivity_separator_issues(rel, stripped_lines))
        issues.extend(_mixed_assignment_issues(rel, stripped_lines))
        issues.extend(_assignment_style_issues(rel, stripped_lines))
        issues.extend(_raw_gated_clock_issues(rel, stripped_lines, clock_names))
        issues.extend(_derived_clock_issues(rel, stripped_lines, clock_names))
        issues.extend(_xz_literal_issues(rel, stripped_lines))
        issues.extend(_wire_initialization_issues(rel, stripped_lines))
        issues.extend(_simple_width_issues(rel, stripped_lines, widths))
        issues.extend(_literal_base_width_issues(rel, stripped_lines))
        issues.extend(_for_loop_bound_issues(rel, stripped_lines, constants))
        issues.extend(_simulation_construct_issues(rel, stripped_lines))
    return issues


def _function_task_issues(rel: str, lines: list[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    for index, line in enumerate(lines, start=1):
        match = re.search(r"\b(function|task)\b", line)
        if match:
            if match.group(1) == "function":
                message = (
                    "Verilog function blocks are not allowed in generated RTL; "
                    "MUST_FUNC_NO_RECURSION and MUST_FUNC_NO_NONBLOCKING keep generated logic inline and reviewable."
                )
            else:
                message = (
                    "Verilog task blocks are not allowed in generated RTL; "
                    "MUST_TASK_NO_TIMING_CONTROL keeps generated logic free of task timing hazards."
                )
            issues.append(
                StaticLintIssue(
                    "error",
                    message,
                    rel,
                    index,
                    code="NO_TASK_FUNCTION",
                )
            )
    return issues


def _case_default_issues(rel: str, lines: list[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    stack: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        if re.search(r"\bcase[xz]?\s*\(", line):
            stack.append({"line": index, "has_default": False})
        if stack and re.search(r"\bdefault\s*:", line):
            stack[-1]["has_default"] = True
        if re.search(r"\bendcase\b", line) and stack:
            case = stack.pop()
            if not case["has_default"]:
                issues.append(
                    StaticLintIssue(
                        "error",
                        "Case statement has no default branch; MUST_CASE_HAS_DEFAULT requires an explicit safe default.",
                        rel,
                        int(case["line"]),
                        code="CASE_DEFAULT",
                    )
                )
    return issues


def _case_default_xz_issues(rel: str, lines: list[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    for index, line in enumerate(lines, start=1):
        if re.search(r"\bdefault\s*:\s*[^;]*\d+\s*'\s*[bBoOdDhH][0-9a-fA-F_xXzZ]*[xXzZ]", line):
            issues.append(
                StaticLintIssue(
                    "warning",
                    "REC_CASE_DEFAULT_NOT_XZ requires case default branches to drive deterministic non-x/z values.",
                    rel,
                    index,
                    code="CASE_DEFAULT_XZ",
                )
            )
    return issues


def _casex_casez_issues(rel: str, lines: list[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    for index, line in enumerate(lines, start=1):
        if re.search(r"\bcase[xz]\s*\(", line):
            issues.append(
                StaticLintIssue(
                    "warning",
                    "REC_CASE_NO_CASEX_CASEZ prefers plain case for deterministic simulation.",
                    rel,
                    index,
                    code="CASEX_CASEZ",
                )
            )
    return issues


def _legacy_sensitivity_issues(rel: str, lines: list[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    for index, line in enumerate(lines, start=1):
        match = re.search(r"\balways\s*@\s*\(([^)]*)\)", line)
        if not match:
            continue
        sensitivity = match.group(1).strip()
        lowered = sensitivity.lower()
        if "*" in sensitivity or "posedge" in lowered or "negedge" in lowered:
            continue
        issues.append(
            StaticLintIssue(
                "warning",
                "MUST_SENS_LIST_COMPLETE_MINIMAL requires complete sensitivity coverage; use always @(*) for combinational logic.",
                rel,
                index,
                code="ALWAYS_STAR",
            )
        )
    return issues


def _sensitivity_separator_issues(rel: str, lines: list[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    for index, line in enumerate(lines, start=1):
        match = re.search(r"\balways\s*@\s*\(([^)]*)\)", line)
        if not match:
            continue
        sensitivity = match.group(1)
        if "|" in sensitivity:
            issues.append(
                StaticLintIssue(
                    "error",
                    "MUST_SENS_NO_OR_SEPARATOR forbids `|` or `||` in always sensitivity lists; list signals directly or use always @(*).",
                    rel,
                    index,
                    code="SENS_OR_SEPARATOR",
                )
            )
    return issues


def _mixed_assignment_issues(rel: str, lines: list[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    for start, block in _always_blocks(lines):
        has_blocking = any(re.search(r"(?<![<>=!])=(?!=)", line) for line in block)
        has_nonblocking = any("<=" in line for line in block)
        if has_blocking and has_nonblocking:
            issues.append(
                StaticLintIssue(
                    "warning",
                    "MUST_COMB_BLOCKING_ASSIGN and MUST_SEQ_NONBLOCKING_ASSIGN require separated combinational and sequential assignment intent.",
                    rel,
                    start,
                    code="MIXED_ASSIGN",
                )
            )
    return issues


def _assignment_style_issues(rel: str, lines: list[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    for start, block in _always_blocks(lines):
        header = block[0]
        sequential = bool(re.search(r"\b(posedge|negedge)\b", header, flags=re.IGNORECASE))
        if sequential:
            if any(_has_blocking_assignment(line) for line in block):
                issues.append(
                    StaticLintIssue(
                        "error",
                        "MUST_SEQ_NONBLOCKING_ASSIGN requires nonblocking assignments in sequential always blocks.",
                        rel,
                        start,
                        code="SEQ_BLOCKING_ASSIGN",
                    )
                )
        elif any("<=" in line for line in block):
            issues.append(
                StaticLintIssue(
                    "error",
                    "MUST_COMB_BLOCKING_ASSIGN requires blocking assignments in combinational always blocks.",
                    rel,
                    start,
                    code="COMB_NONBLOCKING_ASSIGN",
                )
            )
    return issues


def _raw_gated_clock_issues(rel: str, lines: list[str], clock_names: set[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    clock_pattern = "|".join(re.escape(name) for name in sorted(clock_names, key=len, reverse=True))
    if not clock_pattern:
        return issues
    patterns = (
        rf"\bassign\s+\w*(?:gclk|gated_clk|clk_gated|clock_gated)\w*\s*=\s*[^;]*\b(?:{clock_pattern})\b\s*[&|]",
        rf"\bassign\s+\w*(?:gclk|gated_clk|clk_gated|clock_gated)\w*\s*=\s*[^;]*[&|]\s*\b(?:{clock_pattern})\b",
        rf"\bwire\s+\w*(?:gclk|gated_clk|clk_gated|clock_gated)\w*\s*=\s*[^;]*\b(?:{clock_pattern})\b\s*[&|]",
        rf"\bwire\s+\w*(?:gclk|gated_clk|clk_gated|clock_gated)\w*\s*=\s*[^;]*[&|]\s*\b(?:{clock_pattern})\b",
    )
    for index, line in enumerate(lines, start=1):
        if any(re.search(pattern, line) for pattern in patterns):
            issues.append(
                StaticLintIssue(
                    "error",
                    "MUST_CLK_NO_COMB_CLOCK forbids raw gated clock logic; use clock-enable RTL or an approved ICG wrapper.",
                    rel,
                    index,
                    code="RAW_GATED_CLOCK",
                )
            )
    return issues


def _derived_clock_issues(rel: str, lines: list[str], clock_names: set[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    for index, line in enumerate(lines, start=1):
        match = re.search(r"\balways\s*@\s*\([^)]*\b(?:posedge|negedge)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
        if not match:
            continue
        name = match.group(1)
        if name not in clock_names and ("clk" in name.lower() or "clock" in name.lower()):
            issues.append(
                StaticLintIssue(
                    "error",
                    "MUST_CLK_NO_COMB_CLOCK and MUST_CLK_NO_REGOUT_CLOCK require confirmed clock ports, not derived clock-like signals.",
                    rel,
                    index,
                    code="DERIVED_CLOCK",
                )
            )
    return issues


def _xz_literal_issues(rel: str, lines: list[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    for index, line in enumerate(lines, start=1):
        if re.search(r"\d+\s*'\s*[bBoOdDhH][0-9a-fA-F_xXzZ]*[xXzZ][0-9a-fA-F_xXzZ]*", line):
            issues.append(
                StaticLintIssue(
                    "error",
                    "MUST_OP_NO_XZ_ARITH, MUST_OP_NO_XZ_CONDITION, and MUST_BRANCH_COND_NO_XZ forbid explicit x/z values in generated RTL logic.",
                    rel,
                    index,
                    code="XZ_LITERAL",
                )
            )
    return issues


def _wire_initialization_issues(rel: str, lines: list[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    for index, line in enumerate(lines, start=1):
        if re.search(r"\bwire\b[^;]*=", line):
            issues.append(
                StaticLintIssue(
                    "error",
                    "MUST_ASSIGN_WIDTH_MATCH-compatible review requires wires to be declared separately from standalone assign statements.",
                    rel,
                    index,
                    code="WIRE_INIT",
                )
            )
    return issues


def _simple_width_issues(rel: str, lines: list[str], widths: dict[str, int]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    for index, line in enumerate(lines, start=1):
        assign_match = re.search(r"\bassign\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]+);", line)
        if assign_match:
            lhs, rhs = assign_match.group(1), assign_match.group(2).strip()
            issues.extend(_width_pair_issue(rel, index, lhs, rhs, widths, "ASSIGN_WIDTH"))
        if re.search(r"\b(if|while)\s*\(|\?", line):
            rel_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*|\d+\s*'\s*[bBoOdDhH][0-9a-fA-F_xXzZ]+)\s*(==|!=|<=|>=|<|>)\s*([A-Za-z_][A-Za-z0-9_]*|\d+\s*'\s*[bBoOdDhH][0-9a-fA-F_xXzZ]+)", line)
            if rel_match:
                left, right = rel_match.group(1), rel_match.group(3)
                issues.extend(_width_pair_issue(rel, index, left, right, widths, "REL_WIDTH"))
    return issues


def _literal_base_width_issues(rel: str, lines: list[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    for index, line in enumerate(lines, start=1):
        if re.search(r"=\s*\d+\s*;", line) and not re.search(r"\d+\s*'\s*[bBoOdDhH]", line):
            issues.append(
                StaticLintIssue(
                    "warning",
                    "REC_LITERAL_EXPLICIT_BASE_WIDTH prefers constants and parameters with explicit width and base.",
                    rel,
                    index,
                    code="LITERAL_BASE_WIDTH",
                )
            )
    return issues


def _width_pair_issue(rel: str, line: int, left: str, right: str, widths: dict[str, int], code: str) -> list[StaticLintIssue]:
    left_width = _expr_width(left, widths)
    right_width = _expr_width(right, widths)
    if left_width is None or right_width is None or left_width == right_width:
        return []
    rule = "MUST_ASSIGN_WIDTH_MATCH" if code == "ASSIGN_WIDTH" else "MUST_OP_REL_WIDTH_MATCH"
    return [
        StaticLintIssue(
            "error",
            f"{rule} requires simple compared or assigned expressions to use matching widths ({left_width} != {right_width}).",
            rel,
            line,
            code=code,
        )
    ]


def _for_loop_bound_issues(rel: str, lines: list[str], constants: set[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    for index, line in enumerate(lines, start=1):
        for_match = re.search(r"\bfor\s*\(([^;]+);([^;]+);([^)]+)\)", line)
        if not for_match:
            continue
        init, cond, step = (part.strip() for part in for_match.groups())
        init_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*=\s*([^;]+)", init)
        step_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*=", step)
        loop_var = init_match.group(1) if init_match else ""
        init_value = init_match.group(2).strip() if init_match else ""
        cond_rhs_match = re.search(r"(?:<=|>=|<|>)\s*([A-Za-z_][A-Za-z0-9_]*|\d+\s*'\s*[bBoOdDhH][0-9a-fA-F_]+|\d+)", cond)
        cond_bound = cond_rhs_match.group(1).strip() if cond_rhs_match else ""
        step_var = step_match.group(1) if step_match else ""
        if (
            not loop_var
            or step_var != loop_var
            or not _is_constant_expr(init_value, constants)
            or not _is_constant_expr(cond_bound, constants)
        ):
            issues.append(
                StaticLintIssue(
                    "error",
                    "MUST_LOOP_FOR_CONST_BOUNDS requires constant for-loop bounds and updates to the loop variable.",
                    rel,
                    index,
                    code="FOR_CONST_BOUNDS",
                )
            )
    return issues


def _simulation_construct_issues(rel: str, lines: list[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    patterns = {
        r"\binitial\b": "MUST_INITIAL_FORBIDDEN keeps simulation-only initial blocks out of RTL source.",
        r"\#[0-9]+": "MUST_ASSIGN_NO_DELAY and MUST_TASK_NO_TIMING_CONTROL keep delay controls out of RTL source.",
        r"\$(display|finish|stop)\b": "MUST_TASK_NO_TIMING_CONTROL keeps simulation system tasks out of RTL source.",
    }
    for index, line in enumerate(lines, start=1):
        for pattern, message in patterns.items():
            if re.search(pattern, line):
                issues.append(StaticLintIssue("error", message, rel, index, code="SIM_ONLY"))
    return issues


def _declared_widths(lines: list[str]) -> dict[str, int]:
    widths: dict[str, int] = {}
    decl_re = re.compile(r"\b(?:input|output|inout|wire|reg)\b(?:\s+reg|\s+wire)?\s*(\[[^]]+\])?\s*([^;]+);")
    ansi_decl_re = re.compile(r"\b(?:input|output|inout|wire|reg)\b(?:\s+reg|\s+wire)?\s*(\[[^]]+\])?\s+([A-Za-z_][A-Za-z0-9_]*)")
    for line in lines:
        for match in ansi_decl_re.finditer(line):
            widths[match.group(2)] = _range_width(match.group(1))
        match = decl_re.search(line)
        if not match:
            continue
        width = _range_width(match.group(1))
        for raw_name in match.group(2).split(","):
            name_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)", raw_name)
            if name_match:
                widths[name_match.group(1)] = width
    return widths


def _constant_names(lines: list[str]) -> set[str]:
    names: set[str] = set()
    for line in lines:
        match = re.search(r"\b(?:parameter|localparam)\b(?:\s+\[[^]]+\])?\s+([^;]+);", line)
        if not match:
            continue
        for raw_name in match.group(1).split(","):
            name_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)", raw_name)
            if name_match:
                names.add(name_match.group(1))
    return names


def _range_width(range_text: str | None) -> int:
    if not range_text:
        return 1
    match = re.search(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", range_text)
    if not match:
        return 1
    left, right = int(match.group(1)), int(match.group(2))
    return abs(left - right) + 1


def _expr_width(expr: str, widths: dict[str, int]) -> int | None:
    expr = expr.strip()
    literal = re.fullmatch(r"(\d+)\s*'\s*[bBoOdDhH][0-9a-fA-F_xXzZ]+", expr)
    if literal:
        return int(literal.group(1))
    identifier = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)", expr)
    if identifier:
        return widths.get(identifier.group(1))
    return None


def _is_constant_expr(expr: str, constants: set[str]) -> bool:
    expr = expr.strip()
    return bool(
        re.fullmatch(r"\d+", expr)
        or re.fullmatch(r"\d+\s*'\s*[bBoOdDhH][0-9a-fA-F_]+", expr)
        or expr in constants
    )


def _has_blocking_assignment(line: str) -> bool:
    normalized = re.sub(r"(==|!=|<=|>=)", "", line)
    return bool(re.search(r"(?<![<>=!])=(?!=)", normalized))


def _always_blocks(lines: list[str]) -> list[tuple[int, list[str]]]:
    blocks: list[tuple[int, list[str]]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not re.search(r"\balways\s*@", line):
            index += 1
            continue
        start = index + 1
        block = [line]
        depth = _begin_delta(line)
        index += 1
        while index < len(lines):
            current = lines[index]
            block.append(current)
            depth += _begin_delta(current)
            index += 1
            if depth <= 0 and re.search(r"\bend\b", current):
                break
            if depth == 0 and ";" in current and not re.search(r"\bbegin\b", block[0]):
                break
        blocks.append((start, block))
    return blocks


def _begin_delta(line: str) -> int:
    begin_count = len(re.findall(r"\bbegin\b", line))
    end_count = len(re.findall(r"\bend\b", line))
    end_count -= len(re.findall(r"\bend(case|module|generate|function|task)\b", line))
    return begin_count - max(end_count, 0)


def _clock_names(spec: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    clock = spec.get("clock") if isinstance(spec.get("clock"), dict) else {}
    if clock.get("name"):
        names.add(str(clock["name"]))
    for port in spec.get("interfaces", {}).get("ports", []) or []:
        if not isinstance(port, dict) or not port.get("name"):
            continue
        name = str(port["name"])
        role = str(port.get("role") or "").lower()
        if role == "clock" or "clk" in name.lower() or "clock" in name.lower():
            names.add(name)
    return names


def _strip_block_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def _strip_line_comments(line: str) -> str:
    return line.split("//", 1)[0]


def _is_testbench(path: Path) -> bool:
    stem = path.stem.lower()
    return stem.endswith("_tb") or stem.startswith("tb_") or "testbench" in stem
