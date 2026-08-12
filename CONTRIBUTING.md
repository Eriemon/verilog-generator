# Contributing

Thank you for improving Verilog Generator. This repository is an agent skill first: changes should help an AI coding agent produce, inspect, repair, and validate Verilog RTL with stronger discipline.

## Contribution Principles

- Keep `SKILL.md` concise and operational.
- Move detailed background, tool behavior, schemas, and long examples into `references/`.
- Keep deterministic workflow logic under `scripts/python/` and stable host-facing imports under `scripts.python.facade`.
- Generate only synthesizable Verilog-2001 RTL and governed verification-side artifacts.
- Do not claim Vivado/xsim, VCS, iverilog, or yosys validation passed unless those tools actually ran.
- Keep generated outputs, temporary reports, local credentials, session traces, and machine-specific paths out of commits.

## Suggested Workflow

1. Open an issue describing the agent behavior, interface pattern, validation problem, or documentation improvement.
2. Make a focused change with a clear before/after behavior.
3. Run the relevant static validation and package checks.
4. Include command output or validation evidence in the pull request.

## Validation

Useful local commands:

```powershell
python -m scripts.python.workflow.cli --version
python -m scripts.python.workflow.cli scaffold --name rtl_adapter --out .\reports\verilog\spec.json
python -m scripts.python.workflow.cli validate --spec .\reports\verilog\spec.json --path .\reports\verilog\generated --no-external
python .\scripts\python\validation\validate_verilog_skill.py --no-require-remote
python .\scripts\build_release.py
```

External HDL tooling is optional for many changes, but required before claiming simulator or implementation-tool acceptance.

## Documentation Expectations

- Keep the default `README.md` in English.
- Put Chinese user-facing documentation in `README-CN.md`.
- Keep examples short, reproducible, and aligned with the skill's Verilog-only scope.
