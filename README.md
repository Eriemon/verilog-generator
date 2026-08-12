<p align="center">
  <a href="README.md"><strong>English</strong></a>
  <span>&nbsp;|&nbsp;</span>
  <a href="README-CN.md">Chinese</a>
</p>

<p align="center">
  <img src="assets/readme/hero.svg" alt="Verilog Generator RTL workbench" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img alt="Apache-2.0 license" src="https://img.shields.io/badge/license-Apache--2.0-2ad4ee"></a>
  <img alt="Version v0.4.0" src="https://img.shields.io/badge/version-v0.4.0-8ce85d">
  <img alt="Verilog-2001" src="https://img.shields.io/badge/RTL-Verilog--2001-ffb85c">
  <img alt="Example gate: zero errors" src="https://img.shields.io/badge/example_gate-0_errors-8ce85d">
</p>

<p align="center">
  Define the ports, clock, reset, and behavior. Get RTL that is readable, traceable, and verifiable.
</p>

## What is Verilog Generator?

Verilog Generator is a Codex skill for everyday Verilog-2001 work. Give it a module requirement, existing RTL, a testbench, or a tool log. It can write a new module, explain old code, add useful comments, or repair code against actual diagnostics.

The project follows one rule: generated RTL should be easy for the next engineer to read, review, and verify. It records what was checked and never presents unrun simulation or synthesis as evidence.

## What can it do?

<table>
  <tr>
    <td width="33%">
      <strong>01 / Write new RTL</strong><br><br>
      Start with ports, clock/reset behavior, timing expectations, and key checks.<br><br>
      <code>Verilog-2001 RTL / testbench / check records</code>
    </td>
    <td width="33%">
      <strong>02 / Understand existing RTL</strong><br><br>
      Hand over a module, testbench, or failure log and get its interfaces, state machines, timing paths, and risks explained.<br><br>
      <code>structure / behavior / risk locations</code>
    </td>
    <td width="33%">
      <strong>03 / Repair with evidence</strong><br><br>
      Fix against expected behavior and real diagnostics while preserving a reviewable diff and validation boundary.<br><br>
      <code>patch / diff / static or real tool evidence</code>
    </td>
  </tr>
</table>

## Generated RTL example

The example below is a 32-bit AXI-Stream meter. It counts valid bytes only when `TVALID` and `TREADY` complete a transfer, counts packets on a transferred `TLAST`, and saturates instead of wrapping.

The complete source uses the `erie_strict` Chinese-first semantic-comment profile. This English excerpt translates those comments while preserving the same transaction conditions, data boundaries, and saturation behavior.

```verilog
// AXI-Stream transaction detection
assign flag_transfer = i_axis_tvalid & i_axis_tready; // Sample only after a valid-ready handshake
assign flag_packet_end = flag_transfer & i_axis_tlast; // End a packet only on a transferred TLAST

// Increment after the last beat transfers, then saturate at the counter limit
always@(posedge i_axis_aclk or negedge i_axis_arstn)begin
	if(i_axis_arstn == 1'b0)begin
		cnt_packet_o <= 32'd0;              // Clear the packet total on asynchronous reset
	end else if(i_clear == 1'b1)begin
		cnt_packet_o <= 32'd0;              // Clear the packet total on a software request
	end else if(flag_packet_end == 1'b1)begin
		if(data_frame_sum[32] == 1'b0)begin
			cnt_packet_o <= data_frame_sum[31:0]; // Store the new total when no carry occurs
		end else begin
			cnt_packet_o <= 32'hFFFF_FFFF;  // Hold the maximum value after saturation
		end
	end else begin
		cnt_packet_o <= cnt_packet_o;       // Keep the total when no packet completes
	end
end
```

**[Open the complete commented source ->](assets/examples/readme/axis_packet_meter.v)**

| Repository-local static gate | Result |
| --- | ---: |
| Formatter AST | `PASS` |
| Readability, comments, naming, profile | `PASS` |
| Errors | `0` |
| Strict warnings | `0` |

> This is static repository-local evidence. No simulator, synthesis, hardware, or remote-tool result is claimed here.

## Installation

> Tell Codex: `Install https://github.com/Eriemon/verilog-generator`

Restart Codex after installation so the skill is discovered.

This README describes the latest public GitHub release, `v0.4.0`. A governed source checkout may be newer; use `VERSION` as the local package version authority and do not infer public release availability from it.

For a pinned install, use tag `v0.4.0` or the automatically generated source archive on the [GitHub release page](https://github.com/Eriemon/verilog-generator/releases/tag/v0.4.0).

## Authors

<p align="center">
  <img src="assets/readme/authors.svg" alt="Jiyuan Liu and He Li, Southeast University HIQC Laboratory" width="100%">
</p>

## How to cite

> If this skill supports your research, teaching, or engineering work, cite the release below. [CITATION.cff](CITATION.cff) remains the canonical metadata source.

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

---

<p align="center">
  <a href="SKILL.md">Skill contract</a>
  <span>&nbsp;&bull;&nbsp;</span>
  <a href="LICENSE">Apache-2.0</a>
  <span>&nbsp;&bull;&nbsp;</span>
  <a href="CITATION.cff">Citation</a>
  <span>&nbsp;&bull;&nbsp;</span>
  <a href="mailto:<REDACTED_EMAIL>">Contact</a>
</p>
