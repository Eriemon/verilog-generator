"""Static interface-contract extraction for Python and Verilog artifacts."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .vectors import extract_vector_hashes, find_vector_contracts

INTERFACE_TARGETS = ("python", "rtl")


def audit_interface(target: str, root: Path) -> dict[str, Any]:
    normalized = _require_target(target)
    if normalized == "python":
        contract = _python_contract(root)
    else:
        contract = _rtl_contract(root)
    contract["interface_sha256"] = _stable_hash(contract)
    return contract


def _require_target(target: str) -> str:
    normalized = target.lower()
    if normalized not in INTERFACE_TARGETS:
        raise ValueError(f"Interface target must be one of {', '.join(INTERFACE_TARGETS)}.")
    return normalized


def _python_contract(root: Path) -> dict[str, Any]:
    functions: list[dict[str, Any]] = []
    issues: list[dict[str, str]] = []
    for path in sorted(root.glob("**/*.py")):
        rel_path = path.relative_to(root).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except SyntaxError as exc:
            issues.append({"severity": "error", "source": "current_module_issue", "message": f"Python parse error: {exc}", "path": rel_path})
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                functions.append({"name": node.name, "args": [arg.arg for arg in node.args.args], "path": rel_path})
    vector_contracts = find_vector_contracts(root)
    return {
        "version": 1,
        "target": "python",
        "source_root": root.name,
        "top": functions[0]["name"] if functions else None,
        "exported_functions": functions,
        "has_run_tests": any(item["name"] == "run_tests" for item in functions),
        "case_ids": _case_ids(vector_contracts),
        "vector_hashes": _vector_hashes(vector_contracts),
        "issues": issues,
    }


def _rtl_contract(root: Path) -> dict[str, Any]:
    text_by_path = _read_files(root, ("*.v",))
    modules: list[dict[str, Any]] = []
    for rel_path, text in text_by_path.items():
        modules.extend(_extract_rtl_modules(text, rel_path))
    top = next((item for item in modules if not item["name"].lower().endswith("_tb")), modules[0] if modules else None)
    vector_contracts = find_vector_contracts(root)
    combined = "\n".join(text_by_path.values())
    return {
        "version": 1,
        "target": "rtl",
        "source_root": root.name,
        "top": top["name"] if top else None,
        "modules": modules,
        "ports": top["ports"] if top else [],
        "instances": [instance for module in modules for instance in module.get("instances", [])],
        "case_ids": _case_ids(vector_contracts) or _scan_case_ids(combined),
        "vector_hashes": _vector_hashes(vector_contracts) or _scan_vector_hashes(text_by_path),
        "issues": [],
    }


def _read_files(root: Path, suffix_globs: tuple[str, ...]) -> dict[str, str]:
    texts: dict[str, str] = {}
    for pattern in suffix_globs:
        for path in sorted(root.glob(f"**/{pattern}")):
            texts[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8", errors="ignore")
    return texts


def _extract_rtl_modules(text: str, rel_path: str) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    pattern = re.compile(r"\bmodule\s+([A-Za-z_][A-Za-z0-9_]*)\s*(?:#\s*\([^;]*?\)\s*)?\((.*?)\)\s*;", re.DOTALL)
    for match in pattern.finditer(text):
        name = match.group(1)
        header = match.group(2)
        body_start = match.end()
        end_match = re.search(r"\bendmodule\b", text[body_start:], re.DOTALL)
        body = text[body_start: body_start + end_match.start()] if end_match else ""
        modules.append(
            {
                "name": name,
                "path": rel_path,
                "ports": _extract_ports(header, body),
                "instances": _extract_instances(body),
            }
        )
    return modules


def _extract_ports(header: str, body: str) -> list[dict[str, Any]]:
    ports: dict[str, dict[str, Any]] = {}
    for raw in header.split(","):
        name_match = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?://.*)?$", raw.strip())
        if name_match:
            ports[name_match.group(1)] = {"name": name_match.group(1), "direction": None, "width": None}
    declaration_re = re.compile(r"\b(input|output|inout)\b\s*(?:reg\s+|wire\s+)?(\[[^\]]+\]\s*)?([^;]+);")
    for match in declaration_re.finditer(body):
        direction = match.group(1)
        width = _width_from_range(match.group(2))
        for name in re.findall(r"\b[A-Za-z_][A-Za-z0-9_]*\b", match.group(3)):
            if name in {"reg", "wire"}:
                continue
            ports.setdefault(name, {"name": name})
            ports[name].update({"direction": direction, "width": width})
    inline_re = re.compile(r"\b(input|output|inout)\b\s*(?:reg\s+|wire\s+)?(\[[^\]]+\]\s*)?([A-Za-z_][A-Za-z0-9_]*)")
    for match in inline_re.finditer(header):
        name = match.group(3)
        ports.setdefault(name, {"name": name})
        ports[name].update({"direction": match.group(1), "width": _width_from_range(match.group(2))})
    return list(ports.values())


def _extract_instances(body: str) -> list[dict[str, str]]:
    instances: list[dict[str, str]] = []
    pattern = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s+(?:#\s*\([^;]*?\)\s*)?([A-Za-z_][A-Za-z0-9_]*)\s*\(", re.DOTALL)
    for match in pattern.finditer(body):
        module, instance = match.group(1), match.group(2)
        if module in {"if", "for", "while", "case", "always", "assign"}:
            continue
        instances.append({"module": module, "instance": instance})
    return instances


def _width_from_range(value: str | None) -> int | None:
    if not value:
        return 1
    match = re.search(r"\[\s*(\d+)\s*:\s*(\d+)\s*\]", value)
    if not match:
        return None
    high, low = int(match.group(1)), int(match.group(2))
    return abs(high - low) + 1


def _case_ids(vector_contracts: list[dict[str, Any]]) -> list[str]:
    case_ids: list[str] = []
    for contract in vector_contracts:
        for case_id in contract.get("case_ids", []) or []:
            if case_id not in case_ids:
                case_ids.append(str(case_id))
    return case_ids


def _vector_hashes(vector_contracts: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for contract in vector_contracts:
        value = contract.get("sha256")
        if value and value not in values:
            values.append(str(value))
    return values


def _scan_case_ids(text: str) -> list[str]:
    return sorted(set(re.findall(r"\bcase_[A-Za-z0-9_]+\b", text)))


def _scan_vector_hashes(text_by_path: dict[str, str]) -> list[str]:
    values: list[str] = []
    for text in text_by_path.values():
        for value in extract_vector_hashes(text):
            if value not in values:
                values.append(value)
    return values


def _stable_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
