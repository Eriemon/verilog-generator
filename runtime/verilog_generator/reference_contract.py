"""Python reference-model contracts and Verilog transcript parsing."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import types
import uuid
from pathlib import Path
from typing import Any

REFERENCE_RESULT_TAG = "VERILOG-GEN-RESULT"


def audit_reference(path: Path) -> dict[str, Any]:
    root = path if path.is_dir() else path.parent
    model_path = _model_path(path)
    module = _load_module(model_path)
    vectors = _reference_vectors(module, root)
    run_tests = getattr(module, "run_tests", None)
    run_case = getattr(module, "run_case", None)
    collect_checkpoints = getattr(module, "collect_checkpoints", None)

    if not callable(run_tests):
        raise ValueError("Python reference model must expose callable run_tests().")
    if not callable(run_case):
        raise ValueError("Python reference model must expose callable run_case(case).")
    if collect_checkpoints is not None and not callable(collect_checkpoints):
        raise ValueError("collect_checkpoints must be callable when present.")

    canonical_cases: list[dict[str, Any]] = []
    output_signature: Any | None = None
    checkpoint_signature: Any | None = None
    output_keys: list[str] = []
    checkpoint_keys: list[str] = []
    for case in _canonical_cases(vectors):
        case_id = _case_id(case)
        outputs_a = _normalize_value(run_case(_clone(case)))
        outputs_b = _normalize_value(run_case(_clone(case)))
        if outputs_a != outputs_b:
            raise ValueError(f"Reference model run_case is non-deterministic for {case_id!r}.")
        checkpoints_a = _normalize_value(collect_checkpoints(_clone(case))) if callable(collect_checkpoints) else None
        checkpoints_b = _normalize_value(collect_checkpoints(_clone(case))) if callable(collect_checkpoints) else None
        if checkpoints_a != checkpoints_b:
            raise ValueError(f"Reference model collect_checkpoints is non-deterministic for {case_id!r}.")

        current_output_signature = _shape_signature(outputs_a)
        output_signature = output_signature or current_output_signature
        if output_signature != current_output_signature:
            raise ValueError(f"Reference model output shape drift was detected at case {case_id!r}.")

        current_checkpoint_signature = _shape_signature(checkpoints_a) if checkpoints_a is not None else None
        if checkpoint_signature is None:
            checkpoint_signature = current_checkpoint_signature
        elif current_checkpoint_signature is not None and checkpoint_signature != current_checkpoint_signature:
            raise ValueError(f"Reference model checkpoint shape drift was detected at case {case_id!r}.")

        for key in _top_level_keys(outputs_a):
            if key not in output_keys:
                output_keys.append(key)
        for key in _top_level_keys(checkpoints_a):
            if key not in checkpoint_keys:
                checkpoint_keys.append(key)

        entry = {
            "case_id": case_id,
            "inputs": _normalize_case_inputs(case),
            "expected_outputs": outputs_a,
        }
        if checkpoints_a is not None:
            entry["checkpoints"] = checkpoints_a
        canonical_cases.append(entry)

    canonical_json = json.dumps({"cases": canonical_cases}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "version": 1,
        "target": "python_reference",
        "model": {
            "path": model_path.name if path.is_file() else model_path.relative_to(root).as_posix(),
            "api_summary": {
                "has_run_tests": True,
                "has_run_case": True,
                "has_collect_checkpoints": callable(collect_checkpoints),
            },
        },
        "case_count": len(canonical_cases),
        "case_ids": [entry["case_id"] for entry in canonical_cases],
        "output_keys": output_keys,
        "checkpoint_keys": checkpoint_keys,
        "cases": canonical_cases,
        "sha256": hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
        "case_sha256": hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
    }


def parse_semantic_transcript(text: str) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or REFERENCE_RESULT_TAG not in line:
            continue
        payload_text = line.split(REFERENCE_RESULT_TAG, 1)[1].strip()
        if payload_text.startswith(":"):
            payload_text = payload_text[1:].strip()
        payload = json.loads(payload_text)
        if not isinstance(payload, dict):
            raise ValueError("Transcript payload must be a JSON object.")
        case_id = str(payload.get("case_id") or payload.get("id") or "")
        if not case_id:
            raise ValueError("Transcript payload is missing case_id.")
        entry = {
            "case_id": case_id,
            "status": str(payload.get("status", "")).upper() or "UNKNOWN",
            "outputs": _normalize_value(payload.get("outputs")),
        }
        if "checkpoints" in payload:
            entry["checkpoints"] = _normalize_value(payload.get("checkpoints"))
        cases.append(entry)
    canonical_json = json.dumps({"cases": cases}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "case_count": len(cases),
        "case_ids": [entry["case_id"] for entry in cases],
        "cases": cases,
        "sha256": hashlib.sha256(canonical_json.encode("utf-8")).hexdigest(),
    }


def compare_reference_to_transcript(reference_contract: dict[str, Any], transcript: dict[str, Any]) -> dict[str, Any]:
    reference_cases = [item for item in reference_contract.get("cases", []) if isinstance(item, dict)]
    transcript_cases = [item for item in transcript.get("cases", []) if isinstance(item, dict)]
    transcript_by_id = {str(item.get("case_id")): item for item in transcript_cases}
    mismatched_cases: list[dict[str, Any]] = []
    checkpoint_drift: list[dict[str, Any]] = []
    failed_cases: list[str] = []
    missing_cases: list[str] = []
    extra_cases = [item.get("case_id") for item in transcript_cases if str(item.get("case_id")) not in {str(ref.get("case_id")) for ref in reference_cases}]
    for reference_case in reference_cases:
        case_id = str(reference_case.get("case_id"))
        transcript_case = transcript_by_id.get(case_id)
        if not transcript_case:
            missing_cases.append(case_id)
            continue
        if str(transcript_case.get("status", "")).upper() != "PASS":
            failed_cases.append(case_id)
        expected_outputs = _normalize_value(reference_case.get("expected_outputs"))
        observed_outputs = _normalize_value(transcript_case.get("outputs"))
        if expected_outputs != observed_outputs:
            mismatched_cases.append(
                {
                    "case_id": case_id,
                    "drift_keys": _drift_keys(expected_outputs, observed_outputs),
                    "expected_outputs": expected_outputs,
                    "observed_outputs": observed_outputs,
                }
            )
        expected_checkpoints = _normalize_value(reference_case.get("checkpoints")) if "checkpoints" in reference_case else None
        observed_checkpoints = _normalize_value(transcript_case.get("checkpoints")) if "checkpoints" in transcript_case else None
        if expected_checkpoints is not None and observed_checkpoints is not None and expected_checkpoints != observed_checkpoints:
            checkpoint_drift.append(
                {
                    "case_id": case_id,
                    "drift_keys": _drift_keys(expected_checkpoints, observed_checkpoints),
                    "expected_checkpoints": expected_checkpoints,
                    "observed_checkpoints": observed_checkpoints,
                }
            )
    order_drift = [case_id for case_id, observed in zip(reference_contract.get("case_ids", []), transcript.get("case_ids", [])) if case_id != observed]
    semantic_ready = not missing_cases and not failed_cases and not mismatched_cases
    localization_confidence = _localization_confidence(mismatched_cases, checkpoint_drift)
    return {
        "semantic_ready": semantic_ready,
        "mismatched_cases": mismatched_cases,
        "checkpoint_drift": checkpoint_drift,
        "failed_cases": failed_cases,
        "missing_cases": missing_cases,
        "extra_cases": [str(item) for item in extra_cases if item],
        "case_order_drift": order_drift,
        "localization_confidence": localization_confidence,
        "reference_sha256": reference_contract.get("sha256"),
        "transcript_sha256": transcript.get("sha256"),
    }


def _model_path(path: Path) -> Path:
    if path.is_file():
        return path
    candidates = sorted({*path.glob("**/*_model.py"), *path.glob("**/model.py")})
    if not candidates:
        raise ValueError("No Python reference model file was found.")
    if len(candidates) > 1:
        raise ValueError("Multiple Python reference model files were found; audit-reference expects exactly one.")
    return candidates[0]


def _load_module(model_path: Path) -> types.ModuleType:
    module_name = f"verilog_generator_reference_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, model_path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load Python reference model from {model_path}.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _reference_vectors(module: types.ModuleType, root: Path) -> list[Any]:
    if hasattr(module, "REFERENCE_VECTORS"):
        vectors = getattr(module, "REFERENCE_VECTORS")
        if not isinstance(vectors, list):
            raise ValueError("REFERENCE_VECTORS must be a list.")
        return vectors
    vector_paths = sorted(root.glob("**/*vectors.json"))
    if not vector_paths:
        raise ValueError("Python reference model must provide REFERENCE_VECTORS or a vectors.json file.")
    payload = json.loads(vector_paths[0].read_text(encoding="utf-8"))
    raw_cases = payload.get("cases", payload) if isinstance(payload, dict) else payload
    if not isinstance(raw_cases, list):
        raise ValueError("Reference vectors JSON must contain a cases list.")
    return raw_cases


def _canonical_cases(vectors: list[Any]) -> list[dict[str, Any]]:
    normalized = [_normalize_value(case) for case in vectors]
    return sorted(normalized, key=lambda case: str(case.get("id") or case.get("name") or json.dumps(case, sort_keys=True, ensure_ascii=False)))


def _clone(value: Any) -> Any:
    return json.loads(json.dumps(_normalize_value(value), ensure_ascii=False))


def _normalize_case_inputs(case: dict[str, Any]) -> Any:
    if not isinstance(case, dict):
        return case
    if "inputs" in case and isinstance(case["inputs"], dict):
        return _normalize_value(case["inputs"])
    if "input" in case:
        return _normalize_value(case["input"])
    return _normalize_value({key: value for key, value in case.items() if key not in {"id", "name", "expected", "outputs", "output"}})


def _case_id(case: dict[str, Any]) -> str:
    return str(case.get("id") or case.get("name") or "case")


def _normalize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(key): _normalize_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize_value(item) for item in value]
    if hasattr(value, "tolist"):
        return _normalize_value(value.tolist())
    if hasattr(value, "item"):
        return _normalize_value(value.item())
    raise ValueError(f"Unsupported value type in semantic contract: {type(value).__name__}")


def _shape_signature(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _shape_signature(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_shape_signature(value[0])] if value else []
    return type(value).__name__


def _top_level_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return list(value.keys())
    return []


def _drift_keys(expected: Any, observed: Any, prefix: str = "") -> list[str]:
    if isinstance(expected, dict) and isinstance(observed, dict):
        keys = sorted(set(expected) | set(observed))
        result: list[str] = []
        for key in keys:
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            if key not in expected or key not in observed:
                result.append(next_prefix)
            else:
                result.extend(_drift_keys(expected[key], observed[key], next_prefix))
        return result
    if isinstance(expected, list) and isinstance(observed, list):
        length = max(len(expected), len(observed))
        result: list[str] = []
        for index in range(length):
            next_prefix = f"{prefix}[{index}]" if prefix else f"[{index}]"
            if index >= len(expected) or index >= len(observed):
                result.append(next_prefix)
            else:
                result.extend(_drift_keys(expected[index], observed[index], next_prefix))
        return result
    if expected != observed:
        return [prefix or "<value>"]
    return []


def _localization_confidence(mismatched_cases: list[dict[str, Any]], checkpoint_drift: list[dict[str, Any]]) -> float:
    if checkpoint_drift:
        return 0.85
    if len(mismatched_cases) == 1:
        return 0.6
    if mismatched_cases:
        return 0.35
    return 1.0

