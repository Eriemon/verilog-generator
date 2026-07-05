<p align="center">
  <a href="README.md">English</a>
  <span>&nbsp;|&nbsp;</span>
  <a href="README-CN.md"><strong>中文</strong></a>
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
  面向 Codex/Agent 的 Verilog-2001 RTL 生成、审查、修复与验证工作流 Skill。
</p>

Verilog Generator 用来把 AI 编程代理变成更可靠的 RTL 与 FPGA 工程助手。它提供触发元数据、工作流指令、确定性 Python helper、结构化风格资产、代表性夹具和验证门禁，帮助 Agent 从确认后的硬件意图稳定推进到可综合 Verilog、自检查 testbench 和有证据的审查产物。

这个仓库是一个公开的 **Agent Skill Package**。从 `v0.4.0` 开始，稳定公开的运行时表面已经切到 `scripts/python/...` 包结构，并以 `SKILL.md` 为主约束入口。

## 为什么需要它

RTL 工作在写代码之前就需要精确确认。Verilog Generator 会要求 Agent 先确认模块名、端口、时钟/复位行为、流水线期望、接口族、参考行为和验证用例，然后再生成产物。

适用场景包括：

- 可综合 Verilog-2001 RTL 模块。
- 自检查 Verilog 或 SystemVerilog 验证产物。
- existing-RTL 的 analyze、compare、improve 和 verify-repair 闭环。
- AXI-Stream、AXI4-Lite、AXI4、AHB、APB、native 或 custom 接口形态。
- 静态验证、仿真就绪检查、workflow trace 和受治理的审查证据。

## Skill 架构

<p align="center">
  <img src="docs/assets/architecture-cn.svg" alt="Verilog Generator Skill 架构" width="100%">
</p>

## 工作流

<p align="center">
  <img src="docs/assets/workflow-cn.svg" alt="Verilog Generator 工作流" width="100%">
</p>

## v0.4.0 重点更新

- 将公开实现主树从 `runtime/verilog_generator/` 迁移到 `scripts/python/...`，让公开仓库和 staged `v0.4.0` skill 包结构一致，不再延续此前的 source-first 形态。
- 把 `scripts.python.workflow.cli` 提升为主要公开执行入口，用于 scaffold、prompt、validate、workflow route、batch 和 existing-RTL 工作流。
- 把既有 RTL 的受控精修接口从 `refine_existing_verilog(...)` 正式切换为 `improve_existing_verilog(...)`，并将稳定 facade 对齐到 `scripts.python.facade.verilog_api`。
- 用 `assets/verilog_pattern_templates/` 替代 `assets/refined_verilog_templates/`，同时把 references 重组为 `references/checklists/`、`references/rules/`、`references/integration/`、`references/skill/` 和 `references/workflows/`。
- 收紧公开发布边界：GitHub release 资产一律从清理后的当前仓库重建，`tmp/` 里的原始压缩包只作为本地导入输入，不会直接上传。

## Breaking Change

`v0.4.0` 是一次明确的 **破坏性升级**，会主动放弃旧的 source-first 公开契约：

- `runtime.verilog_generator` 不再是公开运行时包。
- `integration.verilog_adapter` 不再是公开 facade 路径。
- `scripts/verilog_lint.py`、`scripts/tb_generator.py` 这类顶层 helper wrapper 不再公开发布。
- 这个仓库不再对外提供 `pyproject.toml` 打包契约。

如果你有自动化脚本依赖旧入口，需要迁移到下面的新路径。

## 迁移指南

| 旧公开入口 | 新公开入口 |
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
| 扁平的 `references/*.md` | 分层后的 `references/checklists/`、`references/rules/`、`references/integration/`、`references/skill/`、`references/workflows/` |

## 仓库结构

| 路径 | 作用 |
| --- | --- |
| `SKILL.md` | 面向 Agent 的触发、流程、约束和工具使用规则。 |
| `agents/openai.yaml` | Skill 列表和调用入口的 UI 元数据。 |
| `scripts/python/workflow/` | 主 staged workflow 运行时、CLI handler、路由和产物编排逻辑。 |
| `scripts/python/facade/` | 面向宿主系统的稳定 Python facade。 |
| `scripts/python/existing_rtl/` | existing-RTL analyze、compare、intervention 和 verify-repair helper。 |
| `scripts/python/quality/` | deliverable gate、quality gate、formatter bridge、lint helper 和 comment-only verifier。 |
| `scripts/python/generation/tb_generator.py` | 自检查 testbench scaffold helper。 |
| `assets/verilog_pattern_templates/` | 常见 RTL 结构的紧凑模板提示资产。 |
| `assets/verilog_formatter_config/` | formatter profile、schema 和可复用 gate 模板。 |
| `references/` | rules、integration、workflows、checklists 和 skill standards 的结构化说明。 |
| `docs/assets/` | 仅供 GitHub README 渲染的 SVG 资源，不会进入 release zip。 |
| `RELEASE_RECEIPT.json` | 基于当前清理后仓库重建得到的公开 release 来源记录。 |

## 快速开始

直接告诉你的 AI：请安装 [https://github.com/Eriemon/verilog-generator](https://github.com/Eriemon/verilog-generator)

如果需要固定公开版本，请使用 `v0.4.0` tag 或 GitHub Releases 中重建得到的 `erie-verilog-generator-v0.4.0.zip` 资产。

把本仓库放入 Codex skill 搜索路径即可作为 Agent Skill 使用。做本地 workflow 检查时：

```powershell
python -m scripts.python.workflow.cli --version
python -m scripts.python.workflow.cli scaffold --name rtl_adapter --out .\reports\verilog\spec.json
python -m scripts.python.workflow.cli prompt --spec .\reports\verilog\spec.json --out .\reports\verilog\prompt.md
python -m scripts.python.workflow.cli route-workflow --request-summary "generate an AXI4-Lite CSR block"
```

不依赖外部 HDL 工具的静态验证：

```powershell
python -m scripts.python.workflow.cli validate --spec .\reports\verilog\spec.json --path .\reports\verilog\generated --no-external
python .\scripts\python\quality\verilog_lint.py .\reports\verilog\generated\rtl\rtl_adapter.v
python .\scripts\python\generation\tb_generator.py .\reports\verilog\generated\rtl\rtl_adapter.v --output .\reports\verilog\generated\tb\rtl_adapter_tb.v
```

代表性的 existing-RTL 工作流：

```powershell
python -m scripts.python.workflow.cli analyze-existing --rtl .\assets\examples\existing_rtl\ready_valid_slice.v --out-dir .\reports\existing
python -m scripts.python.workflow.cli improve-existing --rtl .\assets\examples\existing_rtl\ready_valid_slice.v --improve-goal style_improve --out-dir .\reports\improve
python -m scripts.python.workflow.cli verify-existing --rtl .\assets\examples\existing_rtl\ready_valid_slice.v --automation-mode conservative --out-dir .\reports\verify
```

## 公开 Python Facade

稳定 facade 现在位于 `scripts.python.facade.verilog_api`：

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

如果宿主系统想在 Python 层稳定调用 Verilog Generator，而不再依赖旧的 `runtime.*` 或 `integration.*` 布局，应切到这里。

## 发布来源与敏感信息边界

这个仓库现在采用更严格的公开发布边界：

- `tmp/` 里的原始压缩包只作为本地导入输入。
- GitHub release 资产通过 `scripts/build_release.py` 基于当前仓库状态重新构建。
- 重建后的 zip 只包含公开 skill 载荷：`README.md`、`README-CN.md`、`LICENSE`、`SKILL.md`、`VERSION`、`ENGINEERING_DESIGN_GOALS.md`、`RELEASE_RECEIPT.json`、`agents/`、`assets/`、`config/`、`evals/`、`references/`、`scripts/`。
- `docs/assets/`、`CITATION.cff`、`CONTRIBUTING.md`、`SECURITY.md`、`.gitignore`、release helper、本地 settings、缓存运行目录、reports 和私有工作区痕迹都不会进入 release zip。
- 绝对本地路径、本地状态目录、session/bootstrap 痕迹、私有服务器信息、token、password 和 private key 都属于发布阻断项。

## 边界

Verilog Generator 的公开边界是刻意收窄的：

- 面向 Verilog-2001 RTL 及其受治理验证侧产物。
- 不把高层综合流、C/C++ kernel 生成或其他 RTL 方言当作公开 release 保证。
- 优先保持可检查、可审查、可做波形定位的显式逻辑表达。
- 外部验证结论仍然必须来自真实工具执行；Vivado/xsim、VCS、iverilog、yosys 或 remote validation 没跑过，就不能声称对应验证已通过。

## 机构说明

Jiyuan Liu 和 He Li 隶属于东南大学电子科学与工程学院。
两位作者所在团队为东南大学电子科学与工程学院异构智能与量子计算实验室（HIQC 课题组），相关工作面向异构智能、量子计算及相关计算系统研究。

## 联系方式

问题、合作或学术使用，请联系：[erie@seu.edu.cn](mailto:erie@seu.edu.cn)。

## 引用

如果本 skill 对你的研究、教学或工程流程有帮助，请引用。规范引用元数据以 [CITATION.cff](CITATION.cff) 为准。

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

## 许可证

Apache License 2.0，详见 [LICENSE](LICENSE)。
