# Contributing / 贡献指南

Thank you for improving Erie Verilog Generator. This repository is an agent skill first: changes should help an AI coding agent produce, inspect, and validate Verilog RTL with stronger discipline.

感谢你改进 Erie Verilog Generator。本仓库首先是一个 Agent Skill：任何变更都应帮助 AI 编程代理更可靠地生成、检查和验证 Verilog RTL。

## Contribution Principles

- Keep `SKILL.md` concise. Move detailed background, tool behavior, and long examples into `references/`.
- Keep deterministic workflow logic in `runtime/` and stable host-facing APIs in `integration/`.
- Generate only synthesizable Verilog-2001 RTL and self-checking Verilog testbenches.
- Do not claim Vivado/xsim, VCS, iverilog, or yosys validation passed unless those tools actually ran.
- Keep generated outputs, temporary reports, local credentials, and machine-specific paths out of commits.

## Suggested Workflow

1. Open an issue describing the agent behavior, interface pattern, or validation problem.
2. Make a focused change with a clear before/after behavior.
3. Run the relevant static validation and smoke checks.
4. Include command output or validation evidence in the pull request.

## Validation

Useful local commands:

```powershell
python -m runtime.verilog_generator --version
python -m runtime.verilog_generator scaffold --name erie_adapter --out .\reports\verilog\spec.json
python -m runtime.verilog_generator validate --spec .\reports\verilog\spec.json --path .\reports\verilog\generated --no-external
python .\scripts\validate_verilog_skill.py --settings .\config\defaults.json
```

External HDL tooling is optional for many changes, but required before claiming simulator or implementation-tool acceptance.

