"""Command line entrypoint for Verilog generation workflows."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .evaluation import write_eval_metrics
from .evidence import ingest_sources
from .extractor import ExtractionError, extract_response, parse_manifest
from .interface_contract import INTERFACE_TARGETS, audit_interface
from .intervention import resolve_intervention
from .optimizer import build_prompt_memory, optimize_prompt_from_trace
from .planning import decompose_spec
from .prompt import COMMENT_LANGUAGES, PROMPT_BUDGETS, PROMPT_STAGES, render_prompt
from .reference_contract import audit_reference
from .reflection import build_diagnosis, build_intervention, build_repair_plan, generate_repair_prompt
from .spec import SpecError, read_spec, scaffold_spec, write_spec
from .trace import append_trace_event, read_trace, safe_path, spec_summary
from .validation import READINESS_LEVELS, validate_generated
from .vectors import audit_vectors
from .workflow import run_workflow
from .workspace import require_workspace_path, require_write_path, update_workflow_state, write_json, write_text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verilog-gen",
        description="Prompt engineering CLI for Verilog-2001 RTL generation.",
    )
    parser.add_argument("--version", action="version", version="erie-verilog-gen 0.1.2")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scaffold = subparsers.add_parser("scaffold", help="Create a Verilog JSON generation spec template.")
    scaffold.add_argument("--out", required=True, type=Path)
    scaffold.add_argument("--name", help="Optional design name used in generated paths.")
    _add_state_args(scaffold)
    scaffold.set_defaults(func=_cmd_scaffold)

    prompt = subparsers.add_parser("prompt", help="Render a model prompt from a JSON spec.")
    prompt.add_argument("--spec", required=True, type=Path)
    prompt.add_argument("--out", required=True, type=Path)
    prompt.add_argument("--stage", choices=PROMPT_STAGES, default="rtl")
    prompt.add_argument("--context-manifest", type=Path, help="Prior-stage manifest JSON or fenced response.")
    prompt.add_argument("--context-dir", type=Path, help="Directory containing prior-stage artifacts.")
    prompt.add_argument("--evidence", type=Path, help="Evidence JSON for understanding stages.")
    prompt.add_argument("--memory", type=Path, help="Prompt memory JSON to inject into staged prompts.")
    prompt.add_argument("--vector-contract", type=Path, help="Reference vector contract JSON produced by audit-vectors.")
    prompt.add_argument("--decision", type=Path, help="Resolved human decision JSON to inject as a high-priority constraint.")
    prompt.add_argument("--subfunction", help="Restrict staged prompt context to one subfunction and its direct dependencies.")
    prompt.add_argument("--budget", choices=PROMPT_BUDGETS, default="normal")
    prompt.add_argument("--comment-language", choices=COMMENT_LANGUAGES, default="zh")
    prompt.add_argument("--stats-json", type=Path, help="Optional prompt size/context statistics JSON output.")
    _add_trace_args(prompt)
    _add_state_args(prompt)
    prompt.set_defaults(func=_cmd_prompt)

    extract = subparsers.add_parser("extract", help="Extract manifest-listed files from a model response.")
    extract.add_argument("--response", required=True, type=Path)
    extract.add_argument("--out-dir", required=True, type=Path)
    _add_trace_args(extract)
    _add_state_args(extract)
    extract.set_defaults(func=_cmd_extract)

    validate = subparsers.add_parser("validate", help="Validate generated Verilog artifacts.")
    validate.add_argument("--spec", required=True, type=Path)
    validate.add_argument("--path", required=True, type=Path)
    validate.add_argument("--no-external", action="store_true", help="Skip optional external tools even if installed.")
    validate.add_argument("--readiness", choices=READINESS_LEVELS, default="static")
    validate.add_argument("--comment-language", choices=COMMENT_LANGUAGES, default="zh")
    validate.add_argument("--report-json", type=Path, help="Optional structured validation report JSON output.")
    validate.add_argument("--reference-contract", type=Path, help="Optional Python semantic reference contract JSON.")
    _add_trace_args(validate)
    _add_state_args(validate)
    validate.set_defaults(func=_cmd_validate)

    run_workflow_parser = subparsers.add_parser("run-workflow", help="Run or resume an end-to-end staged workflow.")
    run_workflow_parser.add_argument("--spec", type=Path, help="Input spec path for a new run.")
    run_workflow_parser.add_argument("--out-dir", type=Path, help="Run directory for a new workflow execution.")
    run_workflow_parser.add_argument("--resume", type=Path, help="Existing run directory to resume.")
    run_workflow_parser.add_argument("--decision", type=Path, help="Resolved decision JSON for resume or replay.")
    run_workflow_parser.add_argument("--evidence", type=Path, help="Optional evidence JSON used during initial decomposition.")
    run_workflow_parser.add_argument("--model-provider", choices=("mock", "manual", "command"), default="manual")
    run_workflow_parser.add_argument("--model-command", help="External command used by the command provider.")
    run_workflow_parser.add_argument("--readiness", choices=READINESS_LEVELS, default="static")
    run_workflow_parser.add_argument("--max-attempts", type=int, default=3)
    run_workflow_parser.add_argument("--no-external", action="store_true", help="Skip external tool execution during workflow validation.")
    run_workflow_parser.add_argument("--comment-language", choices=COMMENT_LANGUAGES, default="zh")
    run_workflow_parser.add_argument("--model-timeout", type=int, default=120)
    run_workflow_parser.add_argument("--stop-on-human", action=argparse.BooleanOptionalAction, default=True)
    run_workflow_parser.set_defaults(func=_cmd_run_workflow)

    audit_vectors_parser = subparsers.add_parser("audit-vectors", help="Create a semantic contract from reference vectors JSON.")
    audit_vectors_parser.add_argument("--vectors", required=True, type=Path)
    audit_vectors_parser.add_argument("--out", required=True, type=Path)
    _add_state_args(audit_vectors_parser)
    audit_vectors_parser.set_defaults(func=_cmd_audit_vectors)

    audit_interface_parser = subparsers.add_parser("audit-interface", help="Extract a stable interface contract from Python or Verilog artifacts.")
    audit_interface_parser.add_argument("--target", required=True, choices=INTERFACE_TARGETS)
    audit_interface_parser.add_argument("--path", required=True, type=Path)
    audit_interface_parser.add_argument("--out", required=True, type=Path)
    _add_trace_args(audit_interface_parser)
    _add_state_args(audit_interface_parser)
    audit_interface_parser.set_defaults(func=_cmd_audit_interface)

    audit_reference_parser = subparsers.add_parser("audit-reference", help="Execute a Python reference model and emit a semantic reference contract.")
    audit_reference_parser.add_argument("--path", required=True, type=Path)
    audit_reference_parser.add_argument("--out", required=True, type=Path)
    _add_trace_args(audit_reference_parser)
    _add_state_args(audit_reference_parser)
    audit_reference_parser.set_defaults(func=_cmd_audit_reference)

    ingest = subparsers.add_parser("ingest-spec", help="Ingest local text, Markdown, or TeX sources into evidence JSON.")
    ingest.add_argument("--source", required=True, action="append", type=Path)
    ingest.add_argument("--sidecar", action="append", type=Path)
    ingest.add_argument("--out", required=True, type=Path)
    _add_state_args(ingest)
    ingest.set_defaults(func=_cmd_ingest_spec)

    decompose = subparsers.add_parser("decompose", help="Normalize a spec into a subfunction implementation plan.")
    decompose.add_argument("--spec", required=True, type=Path)
    decompose.add_argument("--evidence", type=Path)
    decompose.add_argument("--out", required=True, type=Path)
    _add_state_args(decompose)
    decompose.set_defaults(func=_cmd_decompose)

    reflect = subparsers.add_parser("reflect", help="Create a repair prompt from a validation report and plan.")
    reflect.add_argument("--report", type=Path)
    reflect.add_argument("--report-json", type=Path)
    reflect.add_argument("--plan", required=True, type=Path)
    reflect.add_argument("--out", required=True, type=Path)
    reflect.add_argument("--repair-plan", type=Path)
    reflect.add_argument("--intervention-out", type=Path)
    reflect.add_argument("--diagnosis-out", type=Path)
    _add_trace_args(reflect)
    _add_state_args(reflect)
    reflect.set_defaults(func=_cmd_reflect)

    optimize = subparsers.add_parser("optimize-prompt", help="Generate a targeted prompt patch from trace history.")
    optimize.add_argument("--trace", required=True, type=Path)
    optimize.add_argument("--plan", required=True, type=Path)
    optimize.add_argument("--out", required=True, type=Path)
    optimize.add_argument("--memory-out", type=Path)
    _add_state_args(optimize)
    optimize.set_defaults(func=_cmd_optimize_prompt)

    resolve = subparsers.add_parser("resolve-intervention", help="Convert a human answer into a decision and prompt memory.")
    resolve.add_argument("--intervention", required=True, type=Path)
    resolve.add_argument("--answer", required=True, type=Path)
    resolve.add_argument("--out", required=True, type=Path)
    resolve.add_argument("--memory-out", required=True, type=Path)
    _add_trace_args(resolve)
    _add_state_args(resolve)
    resolve.set_defaults(func=_cmd_resolve_intervention)

    evaluate = subparsers.add_parser("eval", help="Compute workflow metrics from a trace JSONL file.")
    evaluate.add_argument("--trace", required=True, type=Path)
    evaluate.add_argument("--out", required=True, type=Path)
    _add_state_args(evaluate)
    evaluate.set_defaults(func=_cmd_eval)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (ExtractionError, SpecError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _cmd_scaffold(args: argparse.Namespace) -> int:
    out = require_write_path(args.out, purpose="spec output")
    write_spec(out, scaffold_spec("rtl", name=args.name))
    _record_state(args, "scaffold", {"target": "rtl", "output": out})
    return 0


def _cmd_prompt(args: argparse.Namespace) -> int:
    spec_path = require_workspace_path(args.spec, purpose="spec path", must_exist=True)
    spec = read_spec(spec_path, target="rtl")
    context_manifest = _read_manifest(args.context_manifest) if args.context_manifest else None
    evidence = _read_json(args.evidence) if args.evidence else None
    memory = _read_json(args.memory) if args.memory else None
    vector_contract = _read_json(args.vector_contract) if args.vector_contract else None
    decision = _read_json(args.decision) if args.decision else None
    codegen_plan = _resolve_codegen_plan(spec, spec_path)
    output = render_prompt(
        spec,
        target="rtl",
        stage=args.stage,
        context_manifest=context_manifest,
        context_dir=args.context_dir,
        evidence=evidence,
        memory=memory,
        comment_language=args.comment_language,
        vector_contract=vector_contract,
        codegen_plan=codegen_plan,
        subfunction=args.subfunction,
        budget=args.budget,
        decision=decision,
    )
    out = require_write_path(args.out, purpose="prompt output")
    write_text(out, output)
    stats = _prompt_stats(
        output,
        stage=args.stage,
        budget=args.budget,
        subfunction=args.subfunction,
        context_manifest=context_manifest,
        context_dir=args.context_dir,
        vector_contract=vector_contract,
        decision=decision,
    )
    if args.stats_json:
        write_json(require_write_path(args.stats_json, purpose="prompt stats output"), stats)
    _record_state(args, "prompt", {"target": "rtl", "stage": args.stage, "output": out, "stats": stats})
    if args.trace:
        append_trace_event(args.trace, {"event": "prompt", "target": "rtl", "stage": args.stage, "spec": spec_summary(spec), "output": out, "prompt_stats": stats})
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    response_path = require_workspace_path(args.response, purpose="response path", must_exist=True)
    out_dir = require_write_path(args.out_dir, purpose="artifact output directory")
    files = extract_response(response_path.read_text(encoding="utf-8"), out_dir)
    _record_state(args, "extract", {"response": response_path, "out_dir": out_dir, "files": files})
    if args.trace:
        append_trace_event(args.trace, {"event": "extract", "response": response_path, "out_dir": out_dir, "files": [safe_path(path) for path in files]})
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    spec_path = require_workspace_path(args.spec, purpose="spec path", must_exist=True)
    artifact_path = require_workspace_path(args.path, purpose="artifact path", must_exist=True)
    reference_contract = _read_json(args.reference_contract) if args.reference_contract else None
    report = validate_generated(
        read_spec(spec_path, target="rtl"),
        artifact_path,
        target="rtl",
        run_external=not args.no_external,
        readiness=args.readiness,
        comment_language=args.comment_language,
        reference_contract=reference_contract,
    )
    print(report.format())
    if args.report_json:
        write_json(require_write_path(args.report_json, purpose="validation report"), report.to_dict())
    _record_state(args, "validate", {"target": "rtl", "path": artifact_path, "ok": report.ok(), "report_json": args.report_json})
    if args.trace:
        append_trace_event(args.trace, {"event": "validate", "target": "rtl", "path": artifact_path, "ok": report.ok(), "issues": [issue.to_dict() for issue in report.issues]})
    return 0 if report.ok() else 1


def _cmd_run_workflow(args: argparse.Namespace) -> int:
    if args.resume:
        result = run_workflow(
            resume_dir=args.resume,
            decision_path=args.decision,
            stop_on_human=args.stop_on_human,
            run_external=not args.no_external,
            comment_language=args.comment_language,
            model_timeout_s=args.model_timeout,
        )
    else:
        if not args.spec or not args.out_dir:
            raise ValueError("New workflow runs require --spec and --out-dir.")
        result = run_workflow(
            spec_path=args.spec,
            target="rtl",
            out_dir=args.out_dir,
            decision_path=args.decision,
            evidence_path=args.evidence,
            provider_name=args.model_provider,
            provider_command=args.model_command,
            readiness=args.readiness,
            max_attempts=args.max_attempts,
            stop_on_human=args.stop_on_human,
            run_external=not args.no_external,
            comment_language=args.comment_language,
            model_timeout_s=args.model_timeout,
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") == "passed" else 1


def _cmd_audit_vectors(args: argparse.Namespace) -> int:
    vectors_path = require_workspace_path(args.vectors, purpose="vectors path", must_exist=True)
    out = require_write_path(args.out, purpose="vector audit output")
    write_json(out, audit_vectors(vectors_path))
    _record_state(args, "audit_vectors", {"vectors": vectors_path, "output": out})
    return 0


def _cmd_audit_interface(args: argparse.Namespace) -> int:
    artifact_path = require_workspace_path(args.path, purpose="artifact path", must_exist=True)
    out = require_write_path(args.out, purpose="interface audit output")
    contract = audit_interface(args.target, artifact_path)
    write_json(out, contract)
    _record_state(args, "audit_interface", {"target": args.target, "path": artifact_path, "output": out})
    if args.trace:
        append_trace_event(args.trace, {"event": "audit_interface", "target": args.target, "path": artifact_path, "output": out, "interface_sha256": contract.get("interface_sha256")})
    return 0


def _cmd_audit_reference(args: argparse.Namespace) -> int:
    artifact_path = require_workspace_path(args.path, purpose="reference model path", must_exist=True)
    out = require_write_path(args.out, purpose="reference audit output")
    contract = audit_reference(artifact_path)
    write_json(out, contract)
    _record_state(args, "audit_reference", {"path": artifact_path, "output": out})
    if args.trace:
        append_trace_event(args.trace, {"event": "audit_reference", "path": artifact_path, "output": out, "case_count": contract.get("case_count")})
    return 0


def _cmd_ingest_spec(args: argparse.Namespace) -> int:
    root = Path.cwd()
    evidence = ingest_sources(args.source, root, sidecars=args.sidecar or [])
    out = require_write_path(args.out, purpose="evidence output")
    write_json(out, evidence)
    _record_state(args, "ingest_spec", {"sources": args.source, "output": out})
    return 0


def _cmd_decompose(args: argparse.Namespace) -> int:
    spec_path = require_workspace_path(args.spec, purpose="spec path", must_exist=True)
    evidence = _read_json(args.evidence) if args.evidence else None
    plan = decompose_spec(read_spec(spec_path, target="rtl"), target="rtl", evidence=evidence)
    out = require_write_path(args.out, purpose="plan output")
    write_json(out, plan)
    _record_state(args, "decompose", {"spec": spec_path, "output": out})
    return 0


def _cmd_reflect(args: argparse.Namespace) -> int:
    plan = read_spec(require_workspace_path(args.plan, purpose="plan path", must_exist=True), target="rtl")
    report_text = ""
    validation_json = None
    if args.report:
        report_text = require_workspace_path(args.report, purpose="report path", must_exist=True).read_text(encoding="utf-8")
    if args.report_json:
        validation_json = _read_json(args.report_json)
        report_text = report_text or json.dumps(validation_json, indent=2, ensure_ascii=False)
    if not report_text:
        raise ValueError("reflect requires --report or --report-json.")
    trace_events = read_trace(args.trace) if args.trace and args.trace.exists() else []
    repair_prompt = generate_repair_prompt(report_text, plan, trace_events, validation_json, None, None)
    out = require_write_path(args.out, purpose="repair prompt output")
    write_text(out, repair_prompt)
    repair_plan = build_repair_plan(report_text, plan, trace_events, validation_json, None, None)
    diagnosis = build_diagnosis(plan, trace_events, validation_json, None)
    if args.repair_plan:
        write_json(require_write_path(args.repair_plan, purpose="repair plan output"), repair_plan)
    if args.diagnosis_out:
        write_json(require_write_path(args.diagnosis_out, purpose="diagnosis output"), diagnosis)
    if args.intervention_out and repair_plan.get("action") == "ask_human":
        write_json(require_write_path(args.intervention_out, purpose="intervention output"), build_intervention(repair_plan, report_text, validation_json))
    _record_state(args, "reflect", {"output": out})
    return 0


def _cmd_optimize_prompt(args: argparse.Namespace) -> int:
    trace_path = require_workspace_path(args.trace, purpose="trace path", must_exist=True)
    plan_path = require_workspace_path(args.plan, purpose="plan path", must_exist=True)
    plan = read_spec(plan_path, target="rtl")
    out = require_write_path(args.out, purpose="optimized prompt output")
    write_text(out, optimize_prompt_from_trace(trace_path, plan))
    if args.memory_out:
        write_json(require_write_path(args.memory_out, purpose="prompt memory output"), build_prompt_memory(trace_path, plan))
    _record_state(args, "optimize_prompt", {"trace": trace_path, "plan": plan_path, "output": out})
    return 0


def _cmd_resolve_intervention(args: argparse.Namespace) -> int:
    intervention_path = require_workspace_path(args.intervention, purpose="intervention path", must_exist=True)
    answer_path = require_workspace_path(args.answer, purpose="answer path", must_exist=True)
    decision, memory = resolve_intervention(_read_json(intervention_path), answer_path.read_text(encoding="utf-8"))
    out = require_write_path(args.out, purpose="decision output")
    memory_out = require_write_path(args.memory_out, purpose="memory output")
    write_json(out, decision)
    write_json(memory_out, memory)
    _record_state(args, "resolve_intervention", {"intervention": intervention_path, "output": out, "memory": memory_out})
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    trace_path = require_workspace_path(args.trace, purpose="trace path", must_exist=True)
    out = require_write_path(args.out, purpose="evaluation output")
    write_eval_metrics(trace_path, out)
    _record_state(args, "eval", {"trace": trace_path, "output": out})
    return 0


def _add_trace_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--trace", type=Path, help="Optional append-only trace JSONL path.")


def _add_state_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state", type=Path, help="Optional workflow-state JSON path.")
    parser.add_argument("--no-state", action="store_true", help="Disable workflow-state updates.")


def _record_state(args: argparse.Namespace, event: str, payload: dict) -> None:
    update_workflow_state(getattr(args, "state", None), event, payload, enabled=not getattr(args, "no_state", False))


def _read_json(path: Path | None) -> dict:
    if path is None:
        return {}
    json_path = require_workspace_path(path, purpose="JSON path", must_exist=True)
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {json_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {json_path}.")
    return data


def _read_manifest(path: Path) -> dict:
    text = require_workspace_path(path, purpose="context manifest", must_exist=True).read_text(encoding="utf-8")
    stripped = text.strip()
    if stripped.startswith("{"):
        return json.loads(stripped)
    return parse_manifest(text)


def _resolve_codegen_plan(spec: dict, spec_path: Path) -> dict:
    plan_path = spec.get("codegen_plan_path")
    if plan_path:
        candidate = (spec_path.parent / plan_path).resolve()
        if candidate.exists():
            return _read_json(candidate)
    return {}


def _prompt_stats(
    output: str,
    *,
    stage: str,
    budget: str,
    subfunction: str | None,
    context_manifest: dict | None,
    context_dir: Path | None,
    vector_contract: dict | None,
    decision: dict | None,
) -> dict[str, object]:
    manifest_artifacts = len(context_manifest.get("files", []) if isinstance(context_manifest, dict) else [])
    context_artifacts = manifest_artifacts + (1 if context_dir else 0)
    return {
        "version": 1,
        "chars": len(output),
        "approx_tokens": max(1, len(output) // 4),
        "context_artifacts": context_artifacts,
        "has_vector_contract": bool(vector_contract),
        "has_decision": bool(decision),
        "budget": budget,
        "subfunction": subfunction,
        "stage": stage,
    }


if __name__ == "__main__":
    raise SystemExit(main())
