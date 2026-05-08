"""Stage contract verification for Verilog workflows."""

from __future__ import annotations

from typing import Any


def verify_stage(
    plan: dict[str, Any],
    from_contract: dict[str, Any],
    to_contract: dict[str, Any],
) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    issues.extend(_contract_issues(from_contract, "from"))
    issues.extend(_contract_issues(to_contract, "to"))
    issues.extend(plan_contract_interface_issues(plan, to_contract))
    issues.extend(_check_cases(from_contract, to_contract))
    ready = not any(item.get("severity") == "error" for item in issues)
    return {
        "version": 1,
        "target": plan.get("target", "rtl"),
        "ready": ready,
        "issues": issues,
        "summary": {
            "from_target": from_contract.get("target"),
            "to_target": to_contract.get("target"),
            "top": to_contract.get("top"),
        },
    }


def plan_contract_interface_issues(plan: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if contract.get("target") != "rtl":
        return issues
    expected_top = plan.get("name")
    observed_top = contract.get("top")
    if expected_top and observed_top and expected_top != observed_top:
        issues.append(_issue("error", f"RTL top mismatch: expected {expected_top!r}, observed {observed_top!r}."))
    expected_ports = {
        str(item.get("name")): item
        for item in plan.get("interfaces", {}).get("ports", [])
        if isinstance(item, dict) and item.get("name")
    }
    observed_ports = {
        str(item.get("name")): item
        for item in contract.get("ports", [])
        if isinstance(item, dict) and item.get("name")
    }
    for name, expected in expected_ports.items():
        observed = observed_ports.get(name)
        if not observed:
            issues.append(_issue("error", f"RTL port {name!r} is missing."))
            continue
        if expected.get("direction") and observed.get("direction") and expected["direction"] != observed["direction"]:
            issues.append(_issue("error", f"RTL port {name!r} direction changed."))
        expected_width = int(expected.get("width", 1) or 1)
        observed_width = observed.get("width")
        if observed_width is not None and int(observed_width) != expected_width:
            issues.append(_issue("error", f"RTL port {name!r} width changed from {expected_width} to {observed_width}."))
    return issues


def _contract_issues(contract: dict[str, Any], side: str) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for item in contract.get("issues", []) or []:
        if isinstance(item, dict):
            issues.append({**item, "side": side})
    return issues


def _check_cases(from_contract: dict[str, Any], to_contract: dict[str, Any]) -> list[dict[str, Any]]:
    expected = set(str(item) for item in from_contract.get("case_ids", []) or [])
    observed = set(str(item) for item in to_contract.get("case_ids", []) or [])
    if expected and observed and expected != observed:
        return [_issue("warning", "Reference case ids differ between stages.", source="testbench_issue")]
    return []


def _issue(severity: str, message: str, *, source: str = "current_module_issue") -> dict[str, Any]:
    return {"severity": severity, "source": source, "message": message}
