"""Plan audit helpers for Spec2RTL-style information dictionaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .planning import decompose_spec


@dataclass(frozen=True)
class AuditIssue:
    severity: str
    message: str
    subfunction: str | None = None

    def format(self) -> str:
        prefix = f"[{self.subfunction}] " if self.subfunction else ""
        return f"- {self.severity.upper()}: {prefix}{self.message}"


def audit_plan(plan: dict[str, Any]) -> list[AuditIssue]:
    normalized = decompose_spec(plan)
    issues: list[AuditIssue] = []
    subfunctions = normalized.get("subfunctions", [])
    if not subfunctions:
        return [AuditIssue("error", "Plan contains no subfunctions.")]

    known_names = {item.get("name") for item in subfunctions if isinstance(item, dict)}
    for index, subfunction in enumerate(subfunctions):
        name = str(subfunction.get("name", f"subfunction_{index + 1}"))
        if not subfunction.get("inputs"):
            issues.append(AuditIssue("warning", "Missing input interface description.", name))
        if not subfunction.get("outputs"):
            issues.append(AuditIssue("warning", "Missing output interface description.", name))
        if index > 0 and not subfunction.get("dependencies"):
            issues.append(AuditIssue("warning", "No dependencies listed for non-initial subfunction.", name))
        for dependency in subfunction.get("dependencies", []):
            if isinstance(dependency, str) and dependency not in known_names:
                issues.append(AuditIssue("warning", f"Dependency {dependency!r} is not another subfunction name.", name))
        for field in ("behavior", "constraints", "test_intent"):
            items = subfunction.get(field, [])
            if not items:
                issues.append(AuditIssue("error", f"Missing {field} entries.", name))
            for item in items:
                issues.extend(_audit_info_item(field, item, name))
        if not subfunction.get("source_references"):
            issues.append(AuditIssue("warning", "Missing source_references for traceability.", name))
    return issues


def render_audit(plan: dict[str, Any]) -> str:
    normalized = decompose_spec(plan)
    issues = audit_plan(normalized)
    matrix = _coverage_matrix(normalized)
    lines = [
        f"# Plan audit: {normalized['name']}",
        "",
        f"- Target: {normalized['target']}",
        f"- Subfunctions: {len(normalized.get('subfunctions', []))}",
        f"- Issues: {len(issues)}",
        "",
        "## Findings",
        "",
    ]
    if issues:
        lines.extend(issue.format() for issue in issues)
    else:
        lines.append("- INFO: No audit issues found.")
    lines.extend(
        [
            "",
            "## Evidence Coverage Matrix",
            "",
            "| Subfunction | Field | Item | Evidence | Verification cases |",
            "| --- | --- | --- | --- | --- |",
            *matrix,
            "",
            "## Required Evidence Model",
            "",
            "- Each behavior, constraint, and test intent should include `id`, `text`, `evidence`, and `verification_cases`.",
            "- Evidence should point to the originating spec paragraph, table, equation, or explicit user requirement.",
        ]
    )
    return "\n".join(lines) + "\n"


def _audit_info_item(field: str, item: Any, subfunction: str) -> list[AuditIssue]:
    issues: list[AuditIssue] = []
    if not isinstance(item, dict):
        return [AuditIssue("error", f"{field} entry is not an information-dictionary object.", subfunction)]
    if not str(item.get("text", "")).strip():
        issues.append(AuditIssue("error", f"{field} entry {item.get('id', '<unknown>')} has empty text.", subfunction))
    if not item.get("evidence"):
        issues.append(AuditIssue("warning", f"{field} entry {item.get('id', '<unknown>')} has no evidence.", subfunction))
    if not item.get("verification_cases"):
        issues.append(
            AuditIssue("warning", f"{field} entry {item.get('id', '<unknown>')} has no verification_cases.", subfunction)
        )
    return issues


def _coverage_matrix(plan: dict[str, Any]) -> list[str]:
    rows: list[str] = []
    for subfunction in plan.get("subfunctions", []):
        name = _escape_cell(str(subfunction.get("name", "<unknown>")))
        for field in ("behavior", "constraints", "test_intent"):
            for item in subfunction.get(field, []):
                if not isinstance(item, dict):
                    rows.append(f"| {name} | {field} | <invalid> | no | no |")
                    continue
                item_id = _escape_cell(str(item.get("id", "<unknown>")))
                evidence = "yes" if item.get("evidence") else "no"
                cases = "yes" if item.get("verification_cases") else "no"
                rows.append(f"| {name} | {field} | {item_id} | {evidence} | {cases} |")
    return rows or ["| <none> | <none> | <none> | no | no |"]


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|")

