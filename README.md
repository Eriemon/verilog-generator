<p align="center">
  <a href="README.md"><strong>English</strong></a>
  <span>&nbsp;|&nbsp;</span>
  <a href="README-CN.md">Chinese</a>
</p>

<p align="center">
  <img src="docs/assets/hero.svg" alt="Verilog Generator" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-1f6feb"></a>
  <img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-2f81f7">
  <img alt="Version" src="https://img.shields.io/badge/version-v0.4.0-7c3aed">
  <a href="SKILL.md"><img alt="Agent Skill" src="https://img.shields.io/badge/agent-skill-16a34a"></a>
  <a href="ENGINEERING_DESIGN_GOALS.md"><img alt="Target" src="https://img.shields.io/badge/target-Verilog--2001-f59e0b"></a>
</p>

<h1 align="center">Verilog Generator</h1>

<p align="center">
  A Codex-ready agent skill for disciplined Verilog-2001 RTL generation, review, repair, and validation.
</p>

Verilog Generator turns an AI coding agent into a more reliable RTL and FPGA engineering assistant. It ships trigger metadata, workflow instructions, deterministic Python helpers, structured style assets, representative fixtures, and validation gates for moving from confirmed hardware intent to synthesizable Verilog, self-checking testbenches, and evidence-backed review artifacts.

This repository is a public **agent skill package**. The stable public runtime surface is now the `scripts/python/...` package layout plus the skill contract in `SKILL.md`.

## Why It Exists

RTL work needs precision before code. Verilog Generator makes the agent confirm module names, ports, clock/reset behavior, pipeline expectations, interface family, reference behavior, and verification cases before producing artifacts.

Use it when an agent needs to work on:

- Synthesizable Verilog-2001 RTL modules.
- Self-checking Verilog or SystemVerilog verification artifacts.
- Existing-RTL analysis, compare, improve, and verify-repair loops.
- AXI-Stream, AXI4-Lite, AXI4, AHB, APB, native, or custom interface shapes.
- Static validation, simulator-readiness checks, workflow traces, and governed review evidence.

## Skill Architecture

<p align="center">
  <img src="docs/assets/architecture.svg" alt="Verilog Generator skill architecture" width="100%">
</p>

## Workflow

<p align="center">
  <img src="docs/assets/workflow.svg" alt="Verilog Generator workflow" width="100%">
</p>

## What's New In v0.4.0

- Migrates the public implementation tree from `runtime/verilog_generator/` to `scripts/python/...`, making the released skill package match the staged `v0.4.0` bundle layout instead of the earlier source-first layout.
- Promotes the `scripts.python.workflow.cli` command set to the primary public execution entry for scaffold, prompt, validation, workflow routing, batch execution, and existing-RTL flows.
- Renames the controlled existing-RTL refinement surface from `refine_existing_verilog(...)` to `improve_existing_verilog(...)`, and aligns the stable facade with `scripts.python.facade.verilog_api`.
- Replaces `assets/refined_verilog_templates/` with `assets/verilog_pattern_templates/`, and reorganizes references into `references/checklists/`, `references/rules/`, `references/integration/`, `references/skill/`, and `references/workflows/`.
- Tightens the public release boundary: GitHub release assets are rebuilt from the sanitized repository state, while the raw archive under `tmp/` is treated only as local import input and is never uploaded directly.

## Breaking Change

`v0.4.0` is a **breaking release**. It intentionally drops the old source-first public contract:

- `runtime.verilog_generator` is no longer the public runtime package.
- `integration.verilog_adapter` is no longer the public facade path.
- Top-level helper wrappers such as `scripts/verilog_lint.py` and `scripts/tb_generator.py` are no longer published.
- The repository no longer presents a `pyproject.toml` packaging contract for the public GitHub release.

Existing automation must migrate to the new `scripts/python/...` paths below.

## Migration Guide

| Old public surface | New public surface |
| --- | --- |
| `python -m runtime.verilog_generator scaffold ...` | `python -m scripts.python.workflow.cli scaffold ...` |
| `python -m runtime.verilog_generator run-workflow ...` | `python -m scripts.python.workflow.cli run-workflow ...` |
| `python -m runtime.verilog_generator run-batch ...` | `python -m scripts.python.workflow.cli run-batch ...` |
| `python -m runtime.verilog_generator validate ...` | `python -m scripts.python.workflow.cli validate ...` |
| `python .\scripts\verilog_lint.py ...` | `python .\scripts\python\quality\verilog_lint.py ...` |
| `python .\scripts\tb_generator.py ...` | `python .\scripts\python\generation\tb_generator.py ...` |
| `integration.verilog_adapter` | `scripts.python.facade.verilog_api` |
| `refine_existing_verilog(...)` | `improve_existing_verilog(...)` |
| `assets/refined_verilog_templates/` | `assets/verilog_pattern_templates/` |
| Flat `references/*.md` mix | Structured `references/checklists/`, `references/rules/`, `references/integration/`, `references/skill/`, `references/workflows/` |

## Repository Map

| Path | Purpose |
| --- | --- |
| `SKILL.md` | Agent-facing routing, workflow, constraints, and tool-usage rules. |
| `agents/openai.yaml` | UI metadata for skill lists and invocation chips. |
| `scripts/python/workflow/` | Primary staged workflow runtime, CLI handlers, routing, and artifact orchestration. |
| `scripts/python/facade/` | Stable Python facade for workflow, quality, and existing-RTL flows. |
| `scripts/python/existing_rtl/` | Existing-RTL analysis, compare, intervention, and verify-repair helpers. |
| `scripts/python/quality/` | Deliverable gate, quality gate, formatter bridge, lint helpers, and comment-only verification. |
| `scripts/python/generation/tb_generator.py` | Self-checking testbench scaffold helper. |
| `assets/verilog_pattern_templates/` | Compact reusable pattern hints for common RTL structures. |
| `assets/verilog_formatter_config/` | Formatter profiles, schema, and reusable gate templates. |
| `references/` | Structured guidance for rules, integration, workflows, checklists, and skill standards. |
| `docs/assets/` | Repository-only SVG assets used by the GitHub README; not shipped in the release zip. |
| `RELEASE_RECEIPT.json` | Rebuilt provenance record for the current sanitized GitHub release artifact. |

## Quick Start

Tell your AI assistant: install [https://github.com/Eriemon/verilog-generator](https://github.com/Eriemon/verilog-generator)

Pin the public release with tag `v0.4.0` or the rebuilt `erie-verilog-generator-v0.4.0.zip` asset from GitHub Releases.

Place this repository in a Codex skill search path to use it as an agent skill. For local workflow checks:

```powershell
python -m scripts.python.workflow.cli --version
python -m scripts.python.workflow.cli scaffold --name rtl_adapter --out .\reports\verilog\spec.json
python -m scripts.python.workflow.cli prompt --spec .\reports\verilog\spec.json --out .\reports\verilog\prompt.md
python -m scripts.python.workflow.cli route-workflow --request-summary "generate an AXI4-Lite CSR block"
```

Static validation without external HDL tools:

```powershell
python -m scripts.python.workflow.cli validate --spec .\reports\verilog\spec.json --path .\reports\verilog\generated --no-external
python .\scripts\python\quality\verilog_lint.py .\reports\verilog\generated\rtl\rtl_adapter.v
python .\scripts\python\generation\tb_generator.py .\reports\verilog\generated\rtl\rtl_adapter.v --output .\reports\verilog\generated\tb\rtl_adapter_tb.v
```

Representative existing-RTL flows:

```powershell
python -m scripts.python.workflow.cli analyze-existing --rtl .\assets\examples\existing_rtl\ready_valid_slice.v --out-dir .\reports\existing
python -m scripts.python.workflow.cli improve-existing --rtl .\assets\examples\existing_rtl\ready_valid_slice.v --improve-goal style_improve --out-dir .\reports\improve
python -m scripts.python.workflow.cli verify-existing --rtl .\assets\examples\existing_rtl\ready_valid_slice.v --automation-mode conservative --out-dir .\reports\verify
```

## Public Python Facade

The stable facade now lives at `scripts.python.facade.verilog_api`:

```python
from scripts.python.facade.verilog_api import (
    analyze_existing_verilog,
    compare_verilog_semantics,
    improve_existing_verilog,
    render_verilog_prompt,
    route_verilog_request,
    run_verilog_batch,
    run_verilog_cases,
    run_verilog_workflow,
    validate_verilog_artifacts,
    verify_existing_verilog,
)
```

This surface is intended for host-side orchestration that wants a stable Python import path without depending on the older `runtime.*` or `integration.*` layout.

## Release Provenance And Sanitization

This repository now uses a stricter public-content boundary:

- The raw archive in `tmp/` is treated only as local import input.
- The public GitHub release asset is rebuilt from the current repository state with a deterministic `scripts/build_release.py` step.
- The rebuilt zip includes only the public skill payload: `README.md`, `README-CN.md`, `LICENSE`, `SKILL.md`, `VERSION`, `ENGINEERING_DESIGN_GOALS.md`, `RELEASE_RECEIPT.json`, `agents/`, `assets/`, `config/`, `evals/`, `references/`, and `scripts/`.
- Repository-only files such as `docs/assets/`, `CITATION.cff`, `CONTRIBUTING.md`, `SECURITY.md`, `.gitignore`, release helpers, local settings, cached runs, reports, and private workspace traces are excluded from the release zip.
- Absolute local paths, local state folders, session/bootstrap traces, private server material, tokens, passwords, and private keys are treated as release blockers.

## Scope

Verilog Generator is intentionally narrow:

- It targets Verilog-2001 RTL and governed verification-side artifacts.
- It does not claim high-level-synthesis flow generation, C/C++ kernel generation, or alternate RTL dialect support as public release guarantees.
- It prefers explicit logic over opaque generated shortcuts so waveform review and static quality checks remain inspectable.
- External validation claims still require real tool execution. Vivado/xsim, VCS, iverilog, yosys, or remote-validation acceptance must not be claimed unless those tools actually ran.

## Affiliation

Jiyuan Liu and He Li are with the School of Electronic Science and Engineering, Southeast University.
They are affiliated with the Heterogeneous Intelligence and Quantum Computing Laboratory (HIQC), which works on heterogeneous intelligence, quantum computing, and related computing systems research.

## Contact

For questions, collaboration, or academic use, contact: [erie@seu.edu.cn](mailto:erie@seu.edu.cn).

## Citation

If this skill helps your research, teaching, or engineering workflow, please cite it. The canonical citation metadata is maintained in [CITATION.cff](CITATION.cff).

```bibtex
@software{liu_2026_verilog_generator,
  author       = {Jiyuan Liu and He Li},
  title        = {{Verilog Generator}: An Agent Skill for Verilog-2001 RTL Workflows},
  year         = {2026},
  version      = {0.4.0},
  date         = {2026-07-05},
  url          = {https://github.com/Eriemon/verilog-generator},
  license      = {Apache-2.0},
  note         = {Agent skill package for disciplined Verilog-2001 RTL workflows}
}
```

## License

Apache License 2.0. See [LICENSE](LICENSE).
