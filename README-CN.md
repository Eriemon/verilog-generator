<p align="center">
  <a href="README.md">English</a>
  <span>&nbsp;|&nbsp;</span>
  <a href="README-CN.md"><strong>中文</strong></a>
</p>

<p align="center">
  <img src="assets/readme/hero-overview-cn.png" alt="Verilog Generator：可读 RTL 到可追溯检查" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img alt="Apache-2.0 许可证" src="https://img.shields.io/badge/license-Apache--2.0-2ad4ee"></a>
  <img alt="版本 v1.2.0" src="https://img.shields.io/badge/version-v1.2.0-8ce85d">
  <img alt="Python 3.10 或更新版本" src="https://img.shields.io/badge/python-3.10%2B-3776ab">
  <img alt="Verilog-2001" src="https://img.shields.io/badge/RTL-Verilog--2001-ffb85c">
  <img alt="Codex 技能" src="https://img.shields.io/badge/target-Codex%20skill-8ce85d">
</p>

<p align="center">
  <strong>可读 RTL → 可追溯检查 → 可安装包</strong><br>
  明确接口、对齐行为，并把真实验证结果交接给下一位工程师。
</p>

Verilog Generator 是一个面向日常 Verilog-2001 工作的 Codex Skill。把模块需求、现有 RTL、testbench 或工具日志交给它，它可以编写新模块、解释旧代码、补充语义注释，或根据真实诊断修复问题。

项目始终保持一条清晰边界：只有实际运行过对应检查，生成的 RTL 才会被称为已经验证。静态审查、仿真、综合和硬件结果各自标明来源，让下一位工程师可以阅读代码、复现结论并审查差异。

## 为什么使用 Verilog Generator

- 从端口、时钟、复位、时序预期和事务规则开始，避免需求停留在模糊提示词。
- 通过稳定命名、语义注释和明确设计画像，保持 Verilog-2001 RTL 可读。
- 从接口、状态、时序路径和风险位置解释现有模块与 testbench。
- 针对真实诊断修复，同时保留清晰的变更边界与验证记录。
- 通过版本化元数据、receipt 和可复现安装路径交付受治理的技能包。

## 01 — 从 RTL 意图开始

![可读 RTL 任务的项目事实](assets/readme/project-facts-cn.png)

先写清模块用途、端口、时钟与复位行为、数据边界和关键检查。工作流会在生成或修复开始前保持这些项目事实可见。

## 02 — 生成前对齐行为

![Verilog-2001 生成的设计画像](assets/readme/design-profile-cn.png)

每个请求都会经过明确的设计画像：接口形状、时序假设、复位语义、状态转换和审查范围。这样生成的 RTL 才有可以逐行核对的设计目的。

## 03 — 让验证贯穿交付

![可读性规则与验证交接](assets/readme/rule-rendering-cn.png)

最终交接同时保留语义注释、可读性规则和验证边界。本地静态结果会明确标为静态；没有运行过的仿真或综合不会被写成已经完成。

## 生成结果示例

下面的示例是一个 32 位 AXI-Stream 计量器。它只在 `TVALID` 与 `TREADY` 完成握手时统计有效字节，在完成传输的 `TLAST` 上统计数据包，并在溢出前进入饱和状态，不发生回卷。

完整源码使用 `erie_strict` 中文优先的语义注释画像。下面的片段保留相同的事务条件、数据边界和饱和策略。

```verilog
// AXI-Stream 事务判定
assign flag_transfer = i_axis_tvalid & i_axis_tready; // 仅在发送方与接收方同时就绪时采样
assign flag_packet_end = flag_transfer & i_axis_tlast; // 完成握手且 TLAST 有效时结束数据包

// 数据包计数器在末拍握手后递增，并在上限处饱和
always@(posedge i_axis_aclk or negedge i_axis_arstn)begin
	if(i_axis_arstn == 1'b0)begin
		cnt_packet_o <= 32'd0;              // 异步复位清空数据包累计值
	end else if(i_clear == 1'b1)begin
		cnt_packet_o <= 32'd0;              // 软件清零请求同步清空数据包统计
	end else if(flag_packet_end == 1'b1)begin
		if(data_frame_sum[32] == 1'b0)begin
			cnt_packet_o <= data_frame_sum[31:0]; // 未产生进位时记录新完成的数据包
		end else begin
			cnt_packet_o <= 32'hFFFF_FFFF;  // 达到上限后保持最大数据包计数
		end
	end else begin
		cnt_packet_o <= cnt_packet_o;       // 当前拍未结束数据包时保持累计值
	end
end
```

**[查看完整注释源码 →](assets/examples/readme/axis_packet_meter.v)**

| 仓库本地静态门禁 | 结果 |
| --- | ---: |
| Formatter AST | `PASS` |
| 可读性、注释、命名、Profile | `PASS` |
| 错误 | `0` |
| 严格警告 | `0` |

> 这里只展示仓库本地静态结果，不声称已经执行仿真、综合、硬件或远程工具验证。

## 开始使用

> 告诉 Codex：`Install https://github.com/Eriemon/verilog-generator`

安装后重启 Codex，让它重新发现该 Skill。需要可复现的本地安装时，使用已验证的 `dist/readable-verilog-generator-v1.2.0/`；`VERSION`、`pyproject.toml` 和 `CITATION.cff` 的包版本保持一致。

公开仓库是 [Eriemon/verilog-generator](https://github.com/Eriemon/verilog-generator)。本地镜像位于 `github/readable-verilog-generator/`，并保留 `.git` 历史。镜像按 `dist/` 中的版本逐个复制建立；它不等同于 GitHub tag、release 或 push。

## 本地开发，谨慎镜像

源码权威目录是 `skills/readable-verilog-generator/`。受治理的 release 流程会先生成 `dist/readable-verilog-generator-vX.Y.Z/` 和 receipt，再进行镜像复制。镜像 helper 只检查现有仓库并复制一个已验证包；commit、tag、push 和 GitHub Release 仍由仓库所有者单独决定。

```text
源码 skill  →  版本化 dist + receipt  →  本地 GitHub 镜像
```

请保持包边界可检查：必需公开文件位于 skill 根目录，生成的运行时状态不进入公开包，README 绘图使用本地 PNG，并提供匹配的中英文版本。

## 作者与引用

Jiyuan Liu、He Li · Southeast University · 东南大学 · HIQC（Heterogeneous Intelligence and Quantum Computing Laboratory）

如果本 Skill 对研究、教学或工程工作有帮助，请引用下面的受治理版本；规范元数据始终以 [CITATION.cff](CITATION.cff) 为准。

```bibtex
@software{liu_2026_verilog_generator,
  author       = {Jiyuan Liu and He Li},
  title        = {{Verilog Generator}: An Agent Skill for Verilog-2001 RTL Workflows},
  year         = {2026},
  version      = {1.2.0},
  date         = {2026-08-12},
  url          = {https://github.com/Eriemon/verilog-generator},
  license      = {Apache-2.0},
  note         = {Agent skill package for disciplined Verilog-2001 RTL workflows}
}
```

---

<p align="center">
  <a href="SKILL.md">Skill 约束</a>
  <span>&nbsp;·&nbsp;</span>
  <a href="LICENSE">Apache-2.0</a>
  <span>&nbsp;·&nbsp;</span>
  <a href="LICENSE">Apache License 2.0</a>
  <span>&nbsp;·&nbsp;</span>
  <a href="CITATION.cff">引用</a>
  <span>&nbsp;·&nbsp;</span>
  <a href="mailto:<REDACTED_EMAIL>">联系</a>
</p>
