#!/usr/bin/env python3
"""Independent static lint wrapper for Erie Verilog RTL."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from runtime.verilog_generator.static_lint import lint_generated_rtl  # noqa: E402

MODE_RTL = "rtl"
MODE_TB = "tb"

EXTERNAL_TOOLS = {
    "verible": ("verible-verilog-lint",),
    "verilator": ("verilator", "--lint-only"),
    "slang": ("slang", "--lint-only"),
}


@dataclass(frozen=True)
class ExternalFinding:
    severity: str
    tool: str
    message: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Erie static lint on a Verilog file.")
    parser.add_argument("file", type=Path, help="RTL or testbench file to lint.")
    parser.add_argument("--mode", choices=(MODE_RTL, MODE_TB), default=MODE_RTL)
    parser.add_argument("--external", choices=("none", "auto", *EXTERNAL_TOOLS.keys()), default="none")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source = args.file.resolve()
    print("Encoding: UTF-8")
    if not source.is_file():
        print(f"ERROR file not found: {source}", file=sys.stderr)
        return 2
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        print(f"ERROR failed to read {source} with UTF-8: {exc}", file=sys.stderr)
        return 2

    issues = run_internal_lint(source.name, text, args.mode)
    external_findings = run_external_lint(source, args.external)
    return report_findings(source, issues, external_findings)


def run_internal_lint(filename: str, text: str, mode: str):
    with tempfile.TemporaryDirectory(prefix="erie-lint-") as temp_dir:
        temp_root = Path(temp_dir)
        temp_name = "lint_target_tb.v" if mode == MODE_TB else "lint_target.v"
        temp_path = temp_root / temp_name
        temp_path.write_text(text, encoding="utf-8")
        return lint_generated_rtl({"name": filename, "interfaces": {"ports": []}}, temp_root)


def select_external_tools(selection: str) -> list[str]:
    if selection == "none":
        return []
    if selection == "auto":
        for tool in ("verible", "verilator", "slang"):
            if shutil.which(EXTERNAL_TOOLS[tool][0]):
                return [tool]
        return []
    return [selection]


def run_external_lint(source: Path, selection: str) -> list[ExternalFinding]:
    findings: list[ExternalFinding] = []
    for tool in select_external_tools(selection):
        binary = EXTERNAL_TOOLS[tool][0]
        if shutil.which(binary) is None:
            findings.append(ExternalFinding("warning", tool, f"{tool} is not installed."))
            continue
        command = [*EXTERNAL_TOOLS[tool], str(source)]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        if result.returncode == 0:
            findings.append(ExternalFinding("info", tool, f"{tool} completed with no reported issues."))
            continue
        snippet = (result.stdout + "\n" + result.stderr).strip().replace("\r", " ")
        findings.append(ExternalFinding("warning", tool, f"{tool} reported issues: {' '.join(snippet.split())[:400]}"))
    return findings


def report_findings(source: Path, issues, external_findings: list[ExternalFinding]) -> int:
    errors = 0
    warnings = 0
    print(f"Lint target: {source}")
    for issue in issues:
        print(f"[{issue.severity.upper()}] [{issue.code}] line={issue.line} path={issue.path} {issue.message}")
        if issue.severity == "error":
            errors += 1
        elif issue.severity == "warning":
            warnings += 1
    for finding in external_findings:
        print(f"[{finding.severity.upper()}] [external:{finding.tool}] {finding.message}")
        if finding.severity == "warning":
            warnings += 1
    print(f"Summary: {errors} error(s), {warnings} warning(s)")
    if errors:
        return 2
    if warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
