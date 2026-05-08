"""Reference-vector semantic contract helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

VECTOR_HASH_TAG = "VERILOG-GEN-VECTORS-SHA256:"


def audit_vectors(vectors_path: Path) -> dict[str, Any]:
    payload = json.loads(vectors_path.read_text(encoding="utf-8"))
    return vector_contract_from_payload(payload, source=str(vectors_path))


def vector_contract_from_payload(payload: Any, *, source: str | None = None) -> dict[str, Any]:
    cases = _cases_from_payload(payload)
    normalized_cases = sorted((_normalize_json(case) for case in cases), key=_case_sort_key)
    canonical = {"cases": normalized_cases}
    canonical_json = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    case_ids = [_case_id(case, index) for index, case in enumerate(normalized_cases, start=1)]
    contract = {
        "version": 1,
        "sha256": hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
        "case_count": len(normalized_cases),
        "case_ids": case_ids,
        "input_keys": _keys_for(normalized_cases, ("inputs", "input")),
        "output_keys": _keys_for(normalized_cases, ("outputs", "expected", "output")),
        "canonical_json": canonical_json,
    }
    if source:
        contract["source"] = source
    return contract


def find_vector_contracts(root: Path) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    for path in sorted(root.glob("**/*vectors.json")):
        try:
            contract = audit_vectors(path)
        except Exception:
            continue
        contract["path"] = path.relative_to(root).as_posix()
        contracts.append(contract)
    return contracts


def extract_vector_hashes(text: str) -> list[str]:
    hashes: list[str] = []
    for line in text.splitlines():
        if VECTOR_HASH_TAG not in line:
            continue
        value = line.split(VECTOR_HASH_TAG, 1)[1].strip().split()[0]
        if value and value not in hashes:
            hashes.append(value)
    return hashes


def _cases_from_payload(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        raw = payload.get("cases", payload.get("vectors", []))
    else:
        raw = payload
    if not isinstance(raw, list):
        raise ValueError("Reference vectors must be a JSON list or an object with a cases list.")
    return raw


def _normalize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_json(item) for item in value]
    return value


def _case_sort_key(case: Any) -> str:
    if isinstance(case, dict):
        return str(case.get("id") or case.get("name") or json.dumps(case, sort_keys=True, ensure_ascii=False))
    return json.dumps(case, sort_keys=True, ensure_ascii=False)


def _case_id(case: Any, index: int) -> str:
    if isinstance(case, dict):
        return str(case.get("id") or case.get("name") or f"case_{index}")
    return f"case_{index}"


def _keys_for(cases: list[Any], candidate_fields: tuple[str, ...]) -> list[str]:
    keys: list[str] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        for field in candidate_fields:
            value = case.get(field)
            if isinstance(value, dict):
                for key in value:
                    if str(key) not in keys:
                        keys.append(str(key))
            elif field in case and field not in keys:
                keys.append(field)
    return keys

