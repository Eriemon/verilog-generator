# Ideal/Bad Verilog 语料观察与门禁映射

本文档不是新的规范真源，而是把 `tests/cases` 语料观察、`assets/ideal_bad_style_metrics.json` 统计结果、`scripts/python/quality/quality_gate.py` 的现行实现重新对齐。判断是否已经机器化时，以运行时门禁和测试为准。

## 审计结论

- `VG010`、`VG011`、`VG012`、`VG013`、`VG014`：ideal 语料里稳定出现的方向前缀、参数命名、内部 `_o` 输出桥接，已经是当前命名/端口门禁的一部分。
- `VG004`：Tab 缩进已经是当前格式门禁，不再只是语料偏好。
- `VG031`、`VG052`、`VG061`：复杂模块依赖固定中文区域 banner、输出连线区和实例区组织，这已经进入区域门禁。
- `VG023`、`VG054`：FSM 的 `state_current/state_next`、三段式结构、default 分支与默认保持，已经进入 FSM 门禁。
- `VG040`、`VG041`、`VG055`、`VG056`、`VG066`：语料里“近身语义注释”“不能保留模板注释”的观察，已经转成注释语言、覆盖、占位文本和重复注释门禁。
- `JSON/配置已覆盖`：`assets/ideal_bad_style_metrics.json` 和 `assets/verilog_style_rules.json` 保存了语料抽取后的统计和规则快照；它们是解释依据，不是额外 `VGxxx`。
- `说明性规则`：`always` 多目标驱动在坏例里很常见、坏例只适合 non-strict 分析不适合作为生成目标，这些仍主要是人工判断和生成策略说明，不是单独的新 `VGxxx`。

## Ideal 语料的高信号特征

- 端口稳定使用 `i_`、`o_`、`io_` 前缀。
  - 当前状态：已由 `VG010` 覆盖。
- module parameter 稳定使用 `C_` 大写命名；状态参数稳定使用 `ST_` 大写命名。
  - 当前状态：已由 `VG012` 和 `VG023` 相关规则覆盖。
- 时序/复杂输出稳定使用内部 `_o` 信号，再通过 `assign` bridge 到顶层 `o_` 端口。
  - 当前状态：已由 `VG011`、`VG013`、`VG014`、`VG052` 覆盖。
- 复杂模块大量使用固定中文区域 banner。
  - 当前状态：已由 `VG031`、`VG061` 覆盖。
- 注释普遍贴近 parameter、port、signal、assign、FSM 分支、generate 分支和实例。
  - 当前状态：已由 `VG040`、`VG055`、`VG056`、`VG060`、`VG066` 组合覆盖。
- FSM 稳定使用显式状态参数、状态寄存器段、next-state 段和状态任务/输出段。
  - 当前状态：已由 `VG023`、`VG054` 覆盖。

## Bad 语料的高信号特征

- 大量 legacy 片段缺失双语文件头、区域 banner 和语义注释。
  - 当前状态：文件头/区域/注释相关问题会被 `VG001`、`VG031`、`VG040`、`VG056` 等门禁捕获。
- legacy 端口名常缺少 `i_`/`o_`/`io_` 前缀。
  - 当前状态：已由 `VG010` 覆盖。
- 单个 `always` 常同时驱动多个目标，导致自动重构和注释增强风险高。
  - 当前状态：这是说明性风险信号；相关结构问题会部分落到 `VG023`、`VG054`、区域归属和 deliverable gate，但这里本身不是新增独立门禁。
- `wire x = ...`、缺失 default 分支、块注释、仿真器专用构造经常出现在压力坏例中。
  - 当前状态：块注释已由 `VG002` 覆盖；FSM default 分支已由 `VG054` 覆盖；其余条目仍按现有 deliverable/quality gate 与人工审查共同判断。
- bad/reference fixtures 适合 non-strict 分析或兼容回归，不适合作为直接生成目标。
  - 当前状态：说明性规则，依赖 `tests/cases/manifests/verilog_case_manifest.json` 的 case 角色定义。

## 额外对齐说明

- 模板注释如 `端口信号注释`、`参数解释说明中文` 现在不是“建议修掉”，而是 strict 模式会被 `VG041`/`VG055`/`VG056` 阻断。
- 新生成代码应直接产出完整 Erie header，不应依赖事后人工清理。
  - 当前状态：header 主体由文件头和 formatter 规则约束，但“先生成完整 header”仍属于生成策略说明。
- 复杂端口列表应带中文分组 banner，例如 `全局信号`、`用户接口`、协议接口组。
  - 当前状态：分组 banner 本身已进入 `VG031`/`VG057`/`VG061` 相关区域与协议顺序门禁。

## 真源与复现

- 统计快照：`assets/ideal_bad_style_metrics.json`
- 结构规则真源：`assets/verilog_style_rules.json`
- 运行时门禁：`scripts/python/quality/quality_gate.py`
- 结构抽取：`scripts/python/quality/formatter_ast.py`
- 语料抽取脚本：`scripts/python/corpus/analyze_verilog_style_corpus.py`
- 稳定回归语料：`tests/cases/ideal/rtl`、`tests/cases/bad/rtl`
- 语料角色清单：`tests/cases/manifests/verilog_case_manifest.json`
