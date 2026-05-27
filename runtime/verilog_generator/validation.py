"""Built-in and optional external validation for Verilog artifacts."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .comment_placement import validate_comment_placement
from .config import load_settings
from .interface_contract import audit_interface
from .prompt import require_comment_language
from .reference_contract import REFERENCE_RESULT_TAG, compare_reference_to_transcript, parse_semantic_transcript
from .spec import normalize_spec
from .static_lint import lint_generated_rtl
from .vectors import VECTOR_HASH_TAG, extract_vector_hashes, find_vector_contracts
from .verifier import plan_contract_interface_issues

READINESS_LEVELS = ("static", "compile", "execute", "implement")
ERROR_SOURCES = (
    "spec_issue",
    "dependency_issue",
    "testbench_issue",
    "current_module_issue",
    "insufficient_debug",
    "toolchain_issue",
    "needs_human_intervention",
)
_READINESS_ORDER = {name: index for index, name in enumerate(READINESS_LEVELS)}


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    message: str
    path: str | None = None
    stage: str = "static"
    source: str = "current_module_issue"
    case_id: str | None = None
    tool: str | None = None
    detail: str | None = None

    def format(self) -> str:
        location = f" [{self.path}]" if self.path else ""
        case = f" case={self.case_id}" if self.case_id else ""
        tool = f" tool={self.tool}" if self.tool else ""
        return f"{self.severity.upper()}[{self.source}]{tool}{case}: {self.message}{location}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "stage": self.stage,
            "source": self.source,
            "case_id": self.case_id,
            "tool": self.tool,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ValidationReport:
    target: str
    root: Path
    issues: tuple[ValidationIssue, ...]
    metrics: dict[str, Any] | None = None

    @property
    def errors(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "error")

    @property
    def warnings(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "warning")

    @property
    def skips(self) -> int:
        return sum(1 for issue in self.issues if issue.severity == "skip")

    def ok(self) -> bool:
        return self.errors == 0

    def format(self) -> str:
        lines = [f"Validation report for {self.target} at {self.root}"]
        for stage in READINESS_LEVELS:
            stage_issues = [issue for issue in self.issues if issue.stage == stage]
            if stage_issues:
                lines.append(f"[{stage}]")
                lines.extend(issue.format() for issue in stage_issues)
            elif stage == "static":
                lines.append("[static]")
                lines.append("INFO: Static checks passed.")
        lines.append(f"Summary: {self.errors} error(s), {self.warnings} warning(s), {self.skips} skip(s)")
        if self.metrics:
            lines.append(f"Metrics: {self.metrics}")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "root": str(self.root),
            "ok": self.ok(),
            "errors": self.errors,
            "warnings": self.warnings,
            "skips": self.skips,
            "issues": [issue.to_dict() for issue in self.issues],
            "metrics": self.metrics or {},
        }


def require_readiness(readiness: str) -> str:
    normalized = readiness.lower()
    if normalized not in _READINESS_ORDER:
        raise ValueError(f"Readiness must be one of {', '.join(READINESS_LEVELS)}.")
    return normalized


def readiness_at_least(readiness: str, stage: str) -> bool:
    return _READINESS_ORDER[readiness] >= _READINESS_ORDER[stage]


def validate_generated(
    spec: dict[str, Any],
    path: Path,
    target: str | None = None,
    *,
    run_external: bool = True,
    readiness: str = "static",
    comment_language: str = "zh",
    reference_contract: dict[str, Any] | None = None,
    simulator_config: dict[str, Any] | None = None,
    **_: Any,
) -> ValidationReport:
    normalized = normalize_spec(spec, target=target)
    readiness = require_readiness(readiness)
    comment_language = require_comment_language(comment_language)
    root = path.resolve()
    issues: list[ValidationIssue] = []
    metrics: dict[str, Any] = {}
    if not root.exists():
        issues.append(ValidationIssue("error", "Generated path does not exist.", str(root), source="spec_issue"))
        return ValidationReport("rtl", root, tuple(issues), metrics)

    reference_cases = _reference_case_ids(reference_contract) or _collect_reference_cases(root)
    issues.extend(_unexpected_artifact_issues(normalized, root))
    issues.extend(_validate_expected_outputs(normalized, root))
    issues.extend(_validate_vector_contracts(root))
    issues.extend(_validate_rtl(normalized, root))
    issues.extend(_static_lint_issues(normalized, root))
    issues.extend(_contract_gate_issues(plan_contract_interface_issues(normalized, audit_interface("rtl", root))))
    line_comment_issues, line_comment_metrics = _validate_line_comment_gate(root, comment_language)
    issues.extend(line_comment_issues)
    metrics["line_comment_gate"] = line_comment_metrics
    comment_placement_issues, comment_placement_metrics = _validate_comment_placement_gate(root, comment_language)
    issues.extend(comment_placement_issues)
    metrics["comment_placement_gate"] = comment_placement_metrics
    issues.extend(_validate_rtl_reviewability(root, comment_language))
    issues.extend(_validate_rtl_style_profile(normalized, root))
    issues.extend(_validate_rtl_testbench(normalized, root, reference_cases))
    semantic_issues, semantic_metrics = _validate_semantic_execution(root, reference_contract)
    issues.extend(semantic_issues)
    if semantic_metrics:
        metrics["semantic_execution"] = semantic_metrics
    issues.extend(_validate_placeholders(root, _rtl_files(root)))
    readiness_issues, readiness_metrics = _run_rtl_readiness(normalized, root, readiness, run_external, simulator_config)
    issues.extend(readiness_issues)
    metrics.update(readiness_metrics)
    return ValidationReport("rtl", root, tuple(issues), metrics)


def _contract_gate_issues(raw_issues: list[dict[str, Any]]) -> list[ValidationIssue]:
    return [
        ValidationIssue(
            str(item.get("severity", "error")),
            str(item.get("message", "Interface contract issue.")),
            item.get("path"),
            "static",
            str(item.get("source", "current_module_issue")),
            item.get("case_id"),
        )
        for item in raw_issues
    ]


def _validate_comment_placement_gate(root: Path, comment_language: str) -> tuple[list[ValidationIssue], dict[str, Any]]:
    raw_issues, metrics = validate_comment_placement(root, comment_language)
    issues = [
        ValidationIssue(
            str(item.get("severity", "error")),
            str(item.get("message", "Comment placement issue.")),
            item.get("path"),
            str(item.get("stage", "static")),
            str(item.get("source", "current_module_issue")),
            detail=item.get("detail"),
        )
        for item in raw_issues
    ]
    return issues, metrics


def _static_lint_issues(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for issue in lint_generated_rtl(spec, root):
        detail = f"{issue.code} at line {issue.line}"
        issues.append(
            ValidationIssue(
                issue.severity,
                issue.message,
                issue.path,
                "static",
                issue.source,
                tool="erie_static_lint",
                detail=detail,
            )
        )
    return issues


def _validate_expected_outputs(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for output in spec["outputs"]:
        output_path = root / output["path"]
        if not output_path.exists():
            issues.append(ValidationIssue("error", f"Expected output file is missing: {output['path']}", output["path"], source="spec_issue"))
    return issues


def _rtl_files(root: Path) -> list[Path]:
    return sorted(root.glob("**/*.v"))


def _unexpected_artifact_issues(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    expected_paths: set[str] = set()
    for output in spec.get("outputs", []) or []:
        rel = _output_rel_path(output)
        if not rel:
            continue
        if Path(rel).suffix.lower() != ".v":
            issues.append(ValidationIssue("error", "Spec output must be a Verilog .v file.", rel, source="spec_issue"))
        expected_paths.add(rel)

    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        rel = path.relative_to(root).as_posix()
        if path.suffix.lower() != ".v":
            issues.append(ValidationIssue("error", "Only declared Verilog .v artifacts are allowed.", rel, source="spec_issue"))
        elif rel not in expected_paths:
            issues.append(ValidationIssue("error", "Unexpected Verilog artifact is not declared in spec outputs.", rel, source="spec_issue"))
    return issues


def _output_rel_path(output: dict[str, Any]) -> str | None:
    raw_path = output.get("path") if isinstance(output, dict) else None
    if raw_path in (None, ""):
        return None
    return Path(str(raw_path)).as_posix().lstrip("./")


def _validate_placeholders(root: Path, files: list[Path]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    banned_patterns = {
        r"\bTODO\b": "Placeholder TODO remains in generated code.",
        r"\bFIXME\b": "Placeholder FIXME remains in generated code.",
        r"\.\.\.": "Placeholder ellipsis remains in generated code.",
    }
    for path in files:
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern, message in banned_patterns.items():
            if re.search(pattern, text):
                issues.append(ValidationIssue("error", message, rel))
    return issues


def _validate_rtl(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    rtl_files = _rtl_files(root)
    source_files = [path for path in rtl_files if not _is_testbench(path)]
    if not source_files:
        return [ValidationIssue("error", "No Verilog source files found.")]

    source_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in source_files)
    module_name = spec["name"]
    if not re.search(rf"\bmodule\s+{re.escape(module_name)}\b", source_text):
        issues.append(ValidationIssue("error", f"Top module {module_name!r} was not found."))

    ports = spec.get("interfaces", {}).get("ports", [])
    for port in ports:
        if isinstance(port, dict) and port.get("name") and not re.search(rf"\b{re.escape(str(port['name']))}\b", source_text):
            issues.append(ValidationIssue("warning", f"Port {port['name']!r} was not found in RTL source."))

    reset = spec.get("reset", {}) if isinstance(spec.get("reset"), dict) else {}
    clock = spec.get("clock", {}) if isinstance(spec.get("clock"), dict) else {}
    reset_name = str(reset.get("name", ""))
    clock_name = str(clock.get("name", ""))
    clock_edge = str(clock.get("edge", "")).lower()
    if clock_name and clock_edge in {"posedge", "negedge"}:
        if not re.search(rf"always\s*@\s*\([^)]*\b{clock_edge}\s+{re.escape(clock_name)}\b", source_text, flags=re.IGNORECASE | re.DOTALL):
            issues.append(ValidationIssue("error", f"Clock {clock_name!r} must use {clock_edge} in RTL sensitivity lists."))
    if reset_name and reset.get("synchronous") is True:
        if re.search(rf"always\s*@\s*\([^)]*(?:posedge|negedge)\s+{re.escape(reset_name)}\b", source_text, flags=re.IGNORECASE | re.DOTALL):
            issues.append(ValidationIssue("error", f"Reset {reset_name!r} appears in an always sensitivity list but spec requires synchronous reset."))
    if reset_name and reset.get("synchronous") is False:
        if not re.search(rf"always\s*@\s*\([^)]*(?:posedge|negedge)\s+{re.escape(reset_name)}\b", source_text, flags=re.IGNORECASE | re.DOTALL):
            issues.append(ValidationIssue("error", f"Reset {reset_name!r} must appear in an RTL sensitivity list when spec.reset.synchronous=false."))
    reset_active = str(reset.get("active", "")).lower()
    if reset_name and reset_active in {"low", "high"}:
        low_patterns = (
            rf"if\s*\(\s*!{re.escape(reset_name)}\s*\)",
            rf"if\s*\(\s*{re.escape(reset_name)}\s*==\s*1'b0\s*\)",
        )
        high_patterns = (
            rf"if\s*\(\s*{re.escape(reset_name)}\s*\)",
            rf"if\s*\(\s*{re.escape(reset_name)}\s*==\s*1'b1\s*\)",
        )
        patterns = low_patterns if reset_active == "low" else high_patterns
        if not any(re.search(pattern, source_text, flags=re.IGNORECASE) for pattern in patterns):
            issues.append(ValidationIssue("error", f"Reset {reset_name!r} must use an explicit active-{reset_active} condition in RTL logic."))
    if spec.get("pipeline_required", True) and clock and reset and not re.search(r"\balways\b", source_text):
        issues.append(ValidationIssue("error", "Pipeline-required RTL must include at least one clocked always block."))

    banned_patterns = {
        r"\#[0-9]+": "Delay controls are not synthesizable in RTL source.",
        r"\$display\b": "System task $display should stay out of RTL source.",
        r"\$finish\b": "System task $finish should stay out of RTL source.",
        r"\bforce\b": "force is not synthesizable.",
        r"\brelease\b": "release is not synthesizable.",
    }
    for pattern, message in banned_patterns.items():
        if re.search(pattern, source_text):
            issues.append(ValidationIssue("error", message))
    return issues


def _validate_rtl_testbench(spec: dict[str, Any], root: Path, reference_cases: list[str]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    testbenches = [path for path in _rtl_files(root) if _is_testbench(path)]
    requested = [
        output["path"]
        for output in spec.get("outputs", [])
        if output.get("kind") == "testbench" or "_tb." in str(output.get("path", "")).lower()
    ]
    if requested and not testbenches:
        issues.append(ValidationIssue("error", "No Verilog testbench file found.", source="testbench_issue"))
        return issues
    for path in testbenches:
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "PASS" not in text or "FAIL" not in text:
            issues.append(ValidationIssue("warning", "Testbench should include explicit PASS and FAIL reporting.", rel, source="testbench_issue"))
        for case_id in reference_cases:
            if case_id and case_id not in text:
                issues.append(ValidationIssue("warning", f"Reference case {case_id!r} is not mentioned in the testbench.", rel, source="testbench_issue", case_id=case_id))
    return issues


def _validate_semantic_execution(root: Path, reference_contract: dict[str, Any] | None) -> tuple[list[ValidationIssue], dict[str, Any]]:
    if not reference_contract:
        return [], {}
    testbenches = [path for path in _rtl_files(root) if _is_testbench(path)]
    if not testbenches:
        return [], {}
    combined_text = "\n".join(path.read_text(encoding="utf-8", errors="ignore") for path in testbenches)
    required_case_ids = [str(item) for item in reference_contract.get("case_ids", []) or []]
    checkpoint_keys = [str(item) for item in reference_contract.get("checkpoint_keys", []) or []]
    missing_cases = [case_id for case_id in required_case_ids if case_id not in combined_text]
    missing_checkpoints = [key for key in checkpoint_keys if key not in combined_text]
    transcript = _parse_transcript_from_generated(root)
    metrics: dict[str, Any] = {
        "required_case_ids": required_case_ids,
        "required_checkpoint_keys": checkpoint_keys,
        "semantic_ready": not missing_cases and not missing_checkpoints,
        "mismatched_cases": [],
        "checkpoint_drift": [],
        "failed_cases": [],
        "localization_confidence": 1.0,
    }
    issues: list[ValidationIssue] = []
    for case_id in missing_cases:
        issues.append(
            ValidationIssue(
                "warning",
                f"Reference case {case_id!r} is not covered by the generated testbench transcript scaffolding.",
                stage="static",
                source="testbench_issue",
                case_id=case_id,
            )
        )
        metrics["mismatched_cases"].append({"case_id": case_id, "drift_keys": ["case_id"]})
    for key in missing_checkpoints:
        issues.append(
            ValidationIssue(
                "warning",
                f"Reference checkpoint {key!r} is not preserved in the generated testbench or transcript hints.",
                stage="static",
                source="testbench_issue",
                detail=f"checkpoint={key}",
            )
        )
        metrics["checkpoint_drift"].append({"case_id": "static_check", "drift_keys": [key]})
    if transcript is not None:
        comparison = compare_reference_to_transcript(reference_contract, transcript)
        metrics.update(comparison)
        if comparison.get("checkpoint_drift"):
            metrics["localization_confidence"] = comparison.get("localization_confidence", 0.85)
    elif missing_checkpoints:
        metrics["localization_confidence"] = 0.85
    elif missing_cases:
        metrics["localization_confidence"] = 0.5
    return issues, metrics


def _parse_transcript_from_generated(root: Path) -> dict[str, Any] | None:
    candidates = sorted(root.glob("**/*.log")) + sorted(root.glob("**/*.txt"))
    for path in candidates:
        text = path.read_text(encoding="utf-8", errors="ignore")
        if REFERENCE_RESULT_TAG not in text:
            continue
        try:
            return parse_semantic_transcript(text)
        except ValueError:
            continue
    return None


def _validate_vector_contracts(root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    contracts = find_vector_contracts(root)
    if not contracts:
        return issues
    hashes = set()
    for path in _rtl_files(root):
        hashes.update(extract_vector_hashes(path.read_text(encoding="utf-8", errors="ignore")))
    for contract in contracts:
        expected = str(contract.get("sha256") or "")
        if expected and expected not in hashes:
            issues.append(ValidationIssue("warning", f"Reference vector hash {expected} was not found in Verilog comments.", source="testbench_issue"))
    return issues


def _collect_reference_cases(root: Path) -> list[str]:
    cases: list[str] = []
    for contract in find_vector_contracts(root):
        for case_id in contract.get("case_ids", []) or []:
            if str(case_id) not in cases:
                cases.append(str(case_id))
    return cases


def _reference_case_ids(reference_contract: dict[str, Any] | None) -> list[str]:
    if not reference_contract:
        return []
    return [str(item) for item in reference_contract.get("case_ids", []) or []]


def _validate_rtl_reviewability(root: Path, comment_language: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for path in [item for item in _rtl_files(root) if not _is_testbench(item)]:
        rel = path.relative_to(root).as_posix()
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        if not any("//" in line or "/*" in line for line in lines):
            issues.append(ValidationIssue("warning", "RTL source has no explanatory comments.", rel))
        if comment_language == "zh":
            comments = " ".join(_comment_texts(line) for line in lines)
            if comments and not _contains_cjk(comments):
                issues.append(ValidationIssue("warning", "Expected Chinese explanatory comments.", rel))
    return issues


def _validate_line_comment_gate(root: Path, comment_language: str) -> tuple[list[ValidationIssue], dict[str, int]]:
    issues: list[ValidationIssue] = []
    metrics = {
        "scanned_files": 0,
        "code_lines": 0,
        "commented_code_lines": 0,
        "violations": 0,
    }
    for path in _rtl_files(root):
        rel = path.relative_to(root).as_posix()
        line_infos = _verilog_line_comment_infos(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        metrics["scanned_files"] += 1
        for index, info in enumerate(line_infos):
            if not info["has_code"]:
                continue
            metrics["code_lines"] += 1
            associated_comment = _associated_verilog_comment(line_infos, index)
            if _comment_satisfies_language(associated_comment, comment_language):
                metrics["commented_code_lines"] += 1
                continue
            metrics["violations"] += 1
            if comment_language == "zh":
                message = "Every generated Verilog code line must have a Chinese explanatory comment."
            else:
                message = "Every generated Verilog code line must have an explanatory comment."
            issues.append(
                ValidationIssue(
                    "error",
                    message,
                    f"{rel}:{info['line_no']}",
                    "static",
                    "current_module_issue",
                    detail="Add a same-line explanatory comment for this generated Verilog code line.",
                )
            )
    return issues, metrics


def _associated_verilog_comment(line_infos: list[dict[str, Any]], index: int) -> str:
    same_line_comment = str(line_infos[index]["comment"])
    if same_line_comment:
        return same_line_comment
    for neighbor in (index - 1, index + 1):
        if 0 <= neighbor < len(line_infos) and line_infos[neighbor]["pure_comment"]:
            neighbor_comment = str(line_infos[neighbor]["comment"])
            if neighbor_comment:
                return neighbor_comment
    return ""


def _comment_satisfies_language(comment: str, comment_language: str) -> bool:
    normalized = comment.strip()
    if not normalized:
        return False
    if comment_language == "zh":
        return _contains_cjk(normalized)
    return True


def _verilog_line_comment_infos(lines: list[str]) -> list[dict[str, Any]]:
    infos: list[dict[str, Any]] = []
    in_block_comment = False
    for line_no, line in enumerate(lines, start=1):
        code, comment, in_block_comment = _split_verilog_code_and_comment(line, in_block_comment)
        has_code = bool(code.strip())
        has_comment = bool(comment.strip())
        infos.append(
            {
                "line_no": line_no,
                "has_code": has_code,
                "comment": comment.strip(),
                "pure_comment": has_comment and not has_code,
            }
        )
    return infos


def _split_verilog_code_and_comment(line: str, in_block_comment: bool) -> tuple[str, str, bool]:
    code_parts: list[str] = []
    comment_parts: list[str] = []
    index = 0
    while index < len(line):
        if in_block_comment:
            end_index = line.find("*/", index)
            if end_index == -1:
                comment_parts.append(line[index:])
                return "".join(code_parts), " ".join(comment_parts), True
            comment_parts.append(line[index:end_index])
            index = end_index + 2
            in_block_comment = False
            continue
        if line.startswith("//", index):
            comment_parts.append(line[index + 2 :])
            break
        if line.startswith("/*", index):
            end_index = line.find("*/", index + 2)
            if end_index == -1:
                comment_parts.append(line[index + 2 :])
                return "".join(code_parts), " ".join(comment_parts), True
            comment_parts.append(line[index + 2 : end_index])
            index = end_index + 2
            continue
        code_parts.append(line[index])
        index += 1
    return "".join(code_parts), " ".join(comment_parts), False


def _validate_rtl_style_profile(spec: dict[str, Any], root: Path) -> list[ValidationIssue]:
    if str(spec.get("rtl_style_profile") or "").lower() != "erie_strict":
        return []
    issues: list[ValidationIssue] = []
    required_regions = (
        "配置参数区域",
        "状态参数区域",
        "寄存器信号区域",
        "输出信号区域",
        "输出信号处理区域",
        "主要任务处理区域",
        "模块实例化区域",
    )
    for path in [item for item in _rtl_files(root) if not _is_testbench(item)]:
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "English" not in text or "Chinese" not in text:
            issues.append(ValidationIssue("warning", "Erie strict source should preserve the bilingual header.", rel))
        if not all(marker in text for marker in ("Version:", "Revision Date:", "History:")):
            issues.append(ValidationIssue("warning", "Erie strict source should preserve version/revision/history fields in the bilingual header.", rel))
        for region in required_regions:
            if region not in text:
                issues.append(ValidationIssue("warning", f"Erie strict region {region!r} is missing.", rel))
        if "case(" in text or "case (" in text:
            if "state_current" not in text or "state_next" not in text:
                issues.append(ValidationIssue("warning", "Erie strict FSMs should use `state_current` and `state_next` naming.", rel))
            if not re.search(r"\bST_[A-Za-z0-9_]+\b", text):
                issues.append(ValidationIssue("warning", "Erie strict FSMs should use `ST_*` localparam state names.", rel))
        if re.search(r"generate\b", text) and "gen_" not in text:
            issues.append(ValidationIssue("warning", "Erie strict generate branches should use labels beginning with `gen_`.", rel))
        instance_names = _erie_instance_names(text)
        if instance_names and not any(name.endswith("_Inst") for name in instance_names):
                issues.append(ValidationIssue("warning", "Erie strict module instance names should end with `_Inst`.", rel))
        if not _erie_has_dense_chinese_comments(text):
            issues.append(ValidationIssue("warning", "Erie strict source should keep Chinese explanatory comments near key structure.", rel))
        family = str(spec.get("interface_family") or "")
        if family in {"axi_stream", "axi4", "axi4_lite", "ahb", "apb"} and not _bus_grouping_looks_erie(text, family):
            issues.append(ValidationIssue("warning", f"Erie strict {family} interfaces should keep channel/role port grouping and family-style naming.", rel))
        if re.search(r"\bwire\s+[A-Za-z_][A-Za-z0-9_]*\s*=", text):
            issues.append(ValidationIssue("error", "Declare wires separately from assign statements.", rel))
    return issues


def _erie_has_dense_chinese_comments(text: str) -> bool:
    comment_lines = [line for line in text.splitlines() if "//" in line]
    if not comment_lines:
        return False
    cjk_count = sum(1 for line in comment_lines if _contains_cjk(_comment_texts(line)))
    return cjk_count >= min(6, len(comment_lines))


def _bus_grouping_looks_erie(text: str, family: str) -> bool:
    lower = text.lower()
    if family == "axi_stream":
        return any(token in lower for token in ("//读通道", "//控制通道", "i_m_axis_", "o_m_axis_"))
    if family in {"axi4", "axi4_lite"}:
        return any(token in lower for token in ("//写通道", "//读通道", "i_axi_aw", "i_axi_ar", "o_axi_r", "o_m_axi_", "i_s_axi_"))
    if family == "ahb":
        return any(token in lower for token in ("i_ahb_hclk", "i_ahb_hrstn", "i_ahb_htrans", "//控制通道"))
    if family == "apb":
        return any(token in lower for token in ("i_apb_pclk", "i_apb_prstn", "i_apb_psel", "//控制通道"))
    return True


def _erie_instance_names(text: str) -> list[str]:
    names: list[str] = []
    skip_heads = {"module", "if", "for", "case", "assign", "always", "else", "generate", "endgenerate"}
    patterns = (
        re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", flags=re.MULTILINE),
        re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*#\s*\([^;]*?\)\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(", flags=re.MULTILINE | re.DOTALL),
    )
    for pattern in patterns:
        for match in pattern.finditer(text):
            head = match.group(1)
            instance = match.group(2)
            if head in skip_heads:
                continue
            names.append(instance)
    return names


def _run_rtl_readiness(
    spec: dict[str, Any],
    root: Path,
    readiness: str,
    run_external: bool,
    simulator_config: dict[str, Any] | None = None,
) -> tuple[list[ValidationIssue], dict[str, Any]]:
    sim_config = _simulator_config(simulator_config)
    metrics = {
        "selected_simulator_backend": None,
        "executed_tools": [],
        "missing_preferred_backends": [],
        "selection_policy": sim_config["selection_policy"],
    }
    if readiness == "static":
        return [], metrics
    if not run_external:
        return _optional_tool_skips(_required_tools_for_readiness(readiness, sim_config)), metrics

    issues: list[ValidationIssue] = []
    selection = _select_simulator_backend(sim_config)
    metrics["missing_preferred_backends"] = [item["name"] for item in selection["missing_preferred"]]
    if not selection["backend"]:
        return [_no_simulator_backend_issue(readiness, selection["missing_preferred"])], metrics

    selected = selection["backend"]
    metrics["selected_simulator_backend"] = selected["name"]
    issues.extend(_fallback_warnings(selected["name"], selection["missing_preferred"], readiness))
    sim_issues, executed_tools = _run_simulator_backend(selected["name"], spec, root, readiness)
    issues.extend(sim_issues)
    metrics["executed_tools"] = executed_tools

    source_files = [str(path) for path in _rtl_files(root) if not _is_testbench(path)]
    top = str(spec.get("name") or "")
    if readiness_at_least(readiness, "implement"):
        if _require_tool("yosys", "implement", issues):
            read_cmd = "read_verilog " + " ".join(_yosys_quote(path) for path in source_files)
            issues.extend(_run_tool(["yosys", "-q", "-p", f"{read_cmd}; synth -top {top}; stat"], root, "yosys synthesis", "implement"))
            metrics["executed_tools"].append("yosys")
    return issues, metrics


def _simulator_config(simulator_config: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = simulator_config
    if raw is None:
        try:
            settings = load_settings()
            raw = settings.get("validation", {}).get("simulators", {})
        except Exception:
            raw = {}
    priority = raw.get("priority") if isinstance(raw, dict) else None
    if not isinstance(priority, list) or not priority:
        priority = ["xsim", "vcs_verdi", "iverilog"]
    env_priority = os.environ.get("VERILOG_GENERATOR_SIMULATOR_PRIORITY")
    if env_priority:
        priority = [item.strip() for item in env_priority.split(",") if item.strip()]
    return {
        "selection_policy": str(raw.get("selection_policy", "fallback")) if isinstance(raw, dict) else "fallback",
        "priority": [str(item) for item in priority],
    }


def _select_simulator_backend(sim_config: dict[str, Any]) -> dict[str, Any]:
    missing_preferred: list[dict[str, Any]] = []
    for name in sim_config["priority"]:
        tools = _backend_tools(name)
        if not tools:
            missing_preferred.append({"name": name, "missing_tools": ["<unknown-backend>"]})
            continue
        missing = [tool for tool in tools if shutil.which(tool) is None]
        if missing:
            missing_preferred.append({"name": name, "missing_tools": missing})
            continue
        return {"backend": {"name": name, "tools": tools}, "missing_preferred": missing_preferred}
    return {"backend": None, "missing_preferred": missing_preferred}


def _backend_tools(name: str) -> tuple[str, ...]:
    return {
        "xsim": ("xvlog", "xelab", "xsim"),
        "vcs_verdi": ("vcs", "verdi"),
        "iverilog": ("iverilog", "vvp"),
    }.get(name, ())


def _fallback_warnings(selected: str, missing_preferred: list[dict[str, Any]], readiness: str) -> list[ValidationIssue]:
    warnings: list[ValidationIssue] = []
    stage = "compile" if readiness == "compile" else "execute"
    for item in missing_preferred:
        missing_tools = ", ".join(item["missing_tools"])
        warnings.append(
            ValidationIssue(
                "warning",
                f"Preferred simulator backend {item['name']!r} is unavailable; selected {selected!r}. Missing tools: {missing_tools}.",
                stage=stage,
                source="toolchain_issue",
                tool=item["name"],
            )
        )
    return warnings


def _no_simulator_backend_issue(readiness: str, missing_backends: list[dict[str, Any]]) -> ValidationIssue:
    detail = "; ".join(f"{item['name']}: {', '.join(item['missing_tools'])}" for item in missing_backends)
    return ValidationIssue(
        "error",
        f"No configured simulator backend is available for readiness {readiness!r}. Provide xsim, VCS+Verdi, or iverilog/vvp, or rerun with --no-external.",
        stage="compile" if readiness == "compile" else "execute",
        source="toolchain_issue",
        detail=detail,
    )


def _run_simulator_backend(name: str, spec: dict[str, Any], root: Path, readiness: str) -> tuple[list[ValidationIssue], list[str]]:
    all_files = [str(path) for path in _rtl_files(root)]
    tb_top = _testbench_top(spec)
    if name == "xsim":
        return _run_xsim(all_files, tb_top, readiness)
    if name == "vcs_verdi":
        return _run_vcs_verdi(all_files, readiness)
    if name == "iverilog":
        return _run_iverilog(all_files, readiness)
    return [ValidationIssue("error", f"Unknown simulator backend {name!r}.", stage="compile", source="toolchain_issue", tool=name)], []


def _run_xsim(all_files: list[str], tb_top: str, readiness: str) -> tuple[list[ValidationIssue], list[str]]:
    issues: list[ValidationIssue] = []
    executed_tools: list[str] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        work_dir = Path(temp_dir)
        issues.extend(_run_tool(["xvlog", *all_files], work_dir, "xsim xvlog compile", "compile"))
        executed_tools.append("xvlog")
        if _has_error(issues):
            return issues, executed_tools
        issues.extend(_run_tool(["xelab", tb_top, "-s", "sim_snap"], work_dir, "xsim xelab elaborate", "compile"))
        executed_tools.append("xelab")
        if readiness_at_least(readiness, "execute") and not _has_error(issues):
            issues.extend(_run_tool(["xsim", "sim_snap", "-runall"], work_dir, "xsim simulation", "execute"))
            executed_tools.append("xsim")
    return issues, executed_tools


def _run_vcs_verdi(all_files: list[str], readiness: str) -> tuple[list[ValidationIssue], list[str]]:
    issues: list[ValidationIssue] = []
    executed_tools: list[str] = []
    with tempfile.TemporaryDirectory() as temp_dir:
        work_dir = Path(temp_dir)
        simv = work_dir / ("simv.exe" if sys.platform.startswith("win") else "simv")
        issues.extend(_run_tool(["verdi", "-version"], work_dir, "Verdi availability check", "compile"))
        executed_tools.append("verdi")
        if _has_error(issues):
            return issues, executed_tools
        vcs_command = ["vcs", "-full64", "-o", str(simv), *all_files]
        if readiness_at_least(readiness, "execute"):
            vcs_command.insert(2, "-R")
            issues.extend(_run_tool(vcs_command, work_dir, "VCS simulation", "execute"))
        else:
            issues.extend(_run_tool(vcs_command, work_dir, "VCS compile", "compile"))
        executed_tools.append("vcs")
    return issues, executed_tools


def _run_iverilog(all_files: list[str], readiness: str) -> tuple[list[ValidationIssue], list[str]]:
    issues: list[ValidationIssue] = []
    executed_tools: list[str] = []
    if readiness == "compile":
        issues.extend(_run_tool(["iverilog", "-tnull", *all_files], Path.cwd(), "iverilog compile", "compile"))
        executed_tools.append("iverilog")
        return issues, executed_tools
    with tempfile.TemporaryDirectory() as temp_dir:
        sim_image = Path(temp_dir) / "sim.vvp"
        build_issues = _run_tool(["iverilog", "-o", str(sim_image), *all_files], Path(temp_dir), "iverilog executable build", "execute")
        issues.extend(build_issues)
        executed_tools.append("iverilog")
        if not _has_error(build_issues):
            issues.extend(_run_tool(["vvp", str(sim_image)], Path(temp_dir), "vvp testbench", "execute"))
            executed_tools.append("vvp")
    return issues, executed_tools


def _testbench_top(spec: dict[str, Any]) -> str:
    for output in spec.get("outputs", []) or []:
        if not isinstance(output, dict):
            continue
        path = str(output.get("path", ""))
        if output.get("kind") == "testbench" or "_tb." in path.lower():
            stem = Path(path).stem
            if stem:
                return stem
    return f"{spec.get('name', 'tb')}_tb"


def _has_error(issues: list[ValidationIssue]) -> bool:
    return any(item.severity == "error" for item in issues)


def _required_tools_for_readiness(readiness: str, sim_config: dict[str, Any] | None = None) -> tuple[tuple[str, str], ...]:
    sim_config = sim_config or _simulator_config()
    required: list[tuple[str, str]] = []
    if readiness_at_least(readiness, "compile"):
        stage = "compile" if readiness == "compile" else "execute"
        for backend in sim_config["priority"]:
            for tool in _backend_tools(backend):
                item = (tool, stage)
                if item[0] not in {tool_name for tool_name, _ in required}:
                    required.append(item)
    if readiness_at_least(readiness, "implement"):
        if "yosys" not in {tool for tool, _ in required}:
            required.append(("yosys", "implement"))
    return tuple(required)


def _optional_tool_skips(required_tools: tuple[tuple[str, str], ...]) -> list[ValidationIssue]:
    return [
        ValidationIssue("skip", f"External tool {tool!r} was not run because external validation was disabled.", stage=stage, source="toolchain_issue", tool=tool)
        for tool, stage in required_tools
    ]


def _require_tool(tool_name: str, stage: str, issues: list[ValidationIssue]) -> bool:
    if shutil.which(tool_name):
        return True
    issues.append(ValidationIssue("error", f"External tool {tool_name!r} became unavailable before execution.", stage=stage, source="toolchain_issue", tool=tool_name))
    return False


def _run_tool(command: list[str], root: Path, label: str, stage: str) -> list[ValidationIssue]:
    resolved_tool = shutil.which(command[0])
    run_command = [resolved_tool or command[0], *command[1:]]
    try:
        result = subprocess.run(run_command, cwd=root, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [ValidationIssue("error", f"{label} failed to run: {exc}", stage=stage, source="toolchain_issue", tool=command[0])]
    if result.returncode != 0:
        output = _short_output((result.stderr or result.stdout or "").strip())
        return [ValidationIssue("error", f"{label} failed.", stage=stage, source="toolchain_issue", tool=command[0], detail=output)]
    return []


def _is_testbench(path: Path) -> bool:
    stem = path.stem.lower()
    return stem.endswith("_tb") or stem.startswith("tb_") or "testbench" in stem


def _comment_texts(line: str) -> str:
    if "//" in line:
        return line.split("//", 1)[1]
    return ""


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def _yosys_quote(path: str) -> str:
    return json.dumps(path)


def _short_output(text: str, *, limit: int = 20000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...<truncated>..."
