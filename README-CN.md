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
  <a href="pyproject.toml"><img alt="Python" src="https://img.shields.io/badge/python-3.10%2B-2f81f7"></a>
  <img alt="Version" src="https://img.shields.io/badge/version-v0.3.6-7c3aed">
  <a href="SKILL.md"><img alt="Agent Skill" src="https://img.shields.io/badge/agent-skill-16a34a"></a>
  <a href="ENGINEERING_DESIGN_GOALS.md"><img alt="Target" src="https://img.shields.io/badge/target-Verilog--2001-f59e0b"></a>
</p>

<h1 align="center">Verilog Generator</h1>

<p align="center">
  面向 Codex/Agent 的 Verilog-2001 RTL 专业工作流 Skill。
</p>

Verilog Generator 用来把 AI 编程代理变成更可靠的 RTL 工程助手。它提供触发元数据、工作流指令、接口模板、确定性 runtime、示例和验证门禁，帮助 Agent 从确认后的硬件意图稳定推进到可综合 Verilog 与自检查 testbench。

这个仓库首先是一个 **Agent Skill Package**。Python CLI 是确定性执行层，但主要入口是 Agent 可加载、可遵循的 skill 结构。

## 为什么需要它

RTL 工作在写代码之前就需要精确确认。Verilog Generator 会要求 Agent 先确认模块名、端口、时钟/复位行为、流水线期望、接口族、参考行为和验证用例，然后再生成产物。

适用场景包括：

- 可综合 Verilog-2001 RTL 模块。
- 自检查 Verilog testbench。
- 用于语义比对的 Python reference contract。
- AXI-Stream、AXI4-Lite、AXI4、AHB、APB、native 或 custom 接口形态。
- 静态验证、仿真就绪检查、workflow trace 和生成产物审查。

## Skill 架构

<p align="center">
  <img src="docs/assets/architecture-cn.svg" alt="Verilog Generator Skill 架构" width="100%">
</p>

## 工作流

<p align="center">
  <img src="docs/assets/workflow-cn.svg" alt="Verilog Generator 工作流" width="100%">
</p>

## v0.3.6 重点更新

- 补齐基于 formatter 的风格资产与结构化规则数据：通过 `assets/verilog_formatter_config/`、`assets/verilog_style_rules.json`、`runtime/verilog_generator/formatter_ast.py` 和 `runtime/verilog_generator/rulebook.py`，让格式化、规则解释和质量门共享同一套规则源。
- 新增更严格的可读性与最终交付门：通过 `references/checklists/verilog_readability_gate.md`、`scripts/verilog_generated_deliverable_gate.py`、`runtime/verilog_generator/quality_gate.py`、`runtime/verilog_generator/deliverable_gate.py` 和 `assets/ideal_bad_style_metrics.json`，把“可生成”提升到“可交付”。
- 将 CLI 拆分为按职责划分的命令模块，并补齐 workflow dispatcher：新增 `runtime/verilog_generator/cli_*`、`route-workflow`、`runtime/verilog_generator/workflow_execution.py`、`runtime/verilog_generator/workflow_gates.py`、`runtime/verilog_generator/workflow_stage.py` 以及 `references/workflows/verilog_dispatcher.md`。
- 新增显式依赖与远程验证路由：通过 `scripts/manage_skill_dependencies.py`、`config/defaults.json` 中的 `skill_dependencies` 与 FPGA 路由配置，以及 `references/configuration.md`，让 Agent 能在不硬编码本地 helper 路径的前提下协调 remote SSH 和 FPGA developer 工作流。

## 仓库结构

| 路径 | 作用 |
| --- | --- |
| `SKILL.md` | 面向 Agent 的触发、流程、约束和工具使用规则。 |
| `agents/openai.yaml` | Skill 列表和调用入口的 UI 元数据。 |
| `runtime/verilog_generator/` | scaffold、prompt 渲染、抽取、验证、workflow 路由和分阶段执行辅助模块。 |
| `integration/verilog_adapter.py` | 面向宿主应用的稳定接口。 |
| `assets/verilog_formatter_config/` | formatter profile、schema 和可复用任务模板。 |
| `assets/verilog_style_rules.json` | 供质量门共享的命名、注释和布局规则数据。 |
| `references/checklists/` | 人工审查检查单，包含最终 Verilog readability gate。 |
| `references/workflows/` | generation、modify、comment、analyze、validate 等任务的只读 dispatcher 参考。 |
| `scripts/manage_skill_dependencies.py` | 面向 remote 和 developer skill 的依赖检查、安装、适配与 FPGA 路由辅助脚本。 |
| `scripts/verilog_generated_deliverable_gate.py` | 面向生成 RTL 的最终交付门。 |
| `evals/` | 仓库内 skill-effectiveness 用例，用于 workflow 与 remote-validation 回归检查。 |
| `RELEASE_RECEIPT.json` | 导入的 `v0.3.6` staging 发布包来源记录；GitHub release 资产会基于当前仓库状态重新构建后再上传。 |

## 快速开始

直接告诉你的 AI：请安装 https://github.com/Eriemon/verilog-generator

如果需要固定公开版本，请使用 `v0.3.6` tag 或 GitHub Releases 中重建得到的 `erie-verilog-generator-v0.3.6.zip` 资产。

把本仓库放入 Codex skill 搜索路径即可作为 Agent Skill 使用。开发 runtime 或做本地检查时：

```powershell
python -m runtime.verilog_generator --version
python .\scripts\manage_skill_dependencies.py check --settings .\config\defaults.json
python -m runtime.verilog_generator scaffold --name rtl_adapter --out .\reports\verilog\spec.json
python -m runtime.verilog_generator prompt --spec .\reports\verilog\spec.json --out .\reports\verilog\prompt.md
```

不依赖外部 HDL 工具的静态验证：

```powershell
python -m runtime.verilog_generator validate --spec .\reports\verilog\spec.json --path .\reports\verilog\generated --no-external
python .\scripts\verilog_generated_deliverable_gate.py .\reports\verilog\generated
```

如果宿主系统想在写任何 RTL 文件之前先做只读入口分类，可以使用 `route-workflow`。

外部验证需要真实 HDL 工具。只有实际运行 Vivado/xsim、VCS、iverilog 或 yosys 后，才可以声称对应工具验证通过。

发布来源说明：`v0.3.6` 的 GitHub release 资产是在导入并审查最新 staging 包后，基于当前仓库状态重新构建的。`tmp/` 下的原始压缩包只作为本地导入输入，不会直接上传。

## 集成接口

```python
from integration.verilog_adapter import (
    analyze_existing_verilog,
    compare_verilog_semantics,
    refine_existing_verilog,
    render_verilog_prompt,
    run_verilog_batch,
    run_verilog_workflow,
    validate_verilog_artifacts,
    verify_existing_verilog,
)
```

- `analyze_existing_verilog(...)`：把现有 RTL 分析成稳定 JSON 契约，并输出可复用的设计说明。
- `refine_existing_verilog(...)`：规划 tb scaffold、style refine、partition assist、merge assist、optimize assist 等受控 refine 流程。
- `compare_verilog_semantics(...)`：比较 candidate 与 reference RTL 的接口和 checkpoint 漂移。
- `run_verilog_batch(...)`：在相互隔离的 case run 目录中执行仅生成型 batch 流程。
- `run_verilog_workflow(...)`：运行或恢复分阶段 RTL 工作流。
- `render_verilog_prompt(...)`：宿主系统自行调用模型时渲染 prompt。
- `validate_verilog_artifacts(...)`：下游使用前验证生成 RTL。
- `verify_existing_verilog(...)`：运行 existing-RTL verify-repair 闭环并输出诊断、patch plan 与闭环工件。

## 边界

- 生成 Verilog-2001 `.v` 产物和自检查 Verilog testbench。
- 不生成高层综合流、C/C++ kernel 或其他 RTL 方言。
- 为了更容易进行波形调试，优先使用显式逻辑，而不是 Verilog `function` 和 `task`。
- 本地密钥、私有硬件设计、生成缓存和私有远程服务器细节不应进入仓库。
- 项目本地远程配置应放在 `.settings/` 下，这个公开仓库不会继续保留 repo-tracked `smoke/` 或测试型验证源码目录。

## 机构说明

Jiyuan Liu 和 He Li 隶属于东南大学电子科学与工程学院。
两位作者所在团队为东南大学电子科学与工程学院异构智能与量子计算实验室（HIQC 课题组），相关工作面向异构智能、量子计算及相关计算系统研究。

## 联系方式

问题、合作或学术使用，请联系：[erie@seu.edu.cn](mailto:erie@seu.edu.cn)。

## 引用

本 skill 由东南大学电子科学与工程学院异构智能与量子计算实验室（HIQC 课题组）相关作者维护。

如果本 skill 对你的研究、教学或工程流程有帮助，请引用。规范引用元数据以 [CITATION.cff](CITATION.cff) 为准。

```bibtex
@software{liu_2026_verilog_generator,
  author       = {Jiyuan Liu and He Li},
  title        = {{Verilog Generator}: An Agent Skill for Verilog-2001 RTL Workflows},
  year         = {2026},
  version      = {0.3.6},
  date         = {2026-07-02},
  url          = {https://github.com/Eriemon/verilog-generator},
  license      = {Apache-2.0},
  note         = {Agent skill package for disciplined Verilog-2001 RTL workflows}
}
```

## 许可证

Apache License 2.0，详见 [LICENSE](LICENSE)。
