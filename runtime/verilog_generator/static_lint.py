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
        issues.extend(_function_task_issues(rel, stripped_lines))
        issues.extend(_case_default_issues(rel, stripped_lines))
        issues.extend(_legacy_sensitivity_issues(rel, stripped_lines))
        issues.extend(_mixed_assignment_issues(rel, stripped_lines))
        issues.extend(_raw_gated_clock_issues(rel, stripped_lines, clock_names))
        issues.extend(_simulation_construct_issues(rel, stripped_lines))
    return issues


def _function_task_issues(rel: str, lines: list[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    for index, line in enumerate(lines, start=1):
        if re.search(r"\b(function|task)\b", line):
            issues.append(
                StaticLintIssue(
                    "error",
                    "Verilog function/task blocks are not allowed in generated RTL; inline the logic for reviewability.",
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
                        "warning",
                        "Case statement has no default branch; add an explicit safe default for ASIC review.",
                        rel,
                        int(case["line"]),
                        code="CASE_DEFAULT",
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
                "Legacy combinational always block should use always @(*) to avoid incomplete sensitivity lists.",
                rel,
                index,
                code="ALWAYS_STAR",
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
                    "Always block mixes blocking and nonblocking assignments; split combinational and sequential intent.",
                    rel,
                    start,
                    code="MIXED_ASSIGN",
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
                    "Raw gated clock logic is not ASIC safe; use clock-enable RTL or an approved ICG wrapper.",
                    rel,
                    index,
                    code="RAW_GATED_CLOCK",
                )
            )
    return issues


def _simulation_construct_issues(rel: str, lines: list[str]) -> list[StaticLintIssue]:
    issues: list[StaticLintIssue] = []
    patterns = {
        r"\binitial\b": "Initial blocks are simulation-only and must stay out of RTL source.",
        r"\#[0-9]+": "Delay controls are simulation-only and must stay out of RTL source.",
        r"\$(display|finish|stop)\b": "Simulation system tasks must stay out of RTL source.",
    }
    for index, line in enumerate(lines, start=1):
        for pattern, message in patterns.items():
            if re.search(pattern, line):
                issues.append(StaticLintIssue("error", message, rel, index, code="SIM_ONLY"))
    return issues


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
