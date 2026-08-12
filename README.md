<p align="center">
  <a href="README.md"><strong>English</strong></a>
  <span>&nbsp;|&nbsp;</span>
  <a href="README-CN.md">Chinese</a>
</p>

<p align="center">
  <img src="assets/readme/hero-overview.png" alt="Verilog Generator: from RTL intent to reviewable delivery" width="100%">
</p>

<p align="center">
  <a href="LICENSE"><img alt="Apache-2.0 license" src="https://img.shields.io/badge/license-Apache--2.0-2ad4ee"></a>
  <img alt="VERSION v1.2.0" src="https://img.shields.io/badge/version-v1.2.0-8ce85d">
  <img alt="Verilog-2001" src="https://img.shields.io/badge/RTL-Verilog--2001-ffb85c">
  <img alt="Agent skill" src="https://img.shields.io/badge/target-Codex%20skill-8ce85d">
</p>

<p align="center">
  <strong>Readable RTL → clear behavior → reviewable delivery</strong><br>
  Give AI the design intent, review the behavior preview, and receive artifacts you can inspect and verify.
</p>

Verilog Generator is a Codex skill for practical Verilog-2001 work. It helps you create a module from a clear requirement, understand an existing RTL design, add semantic comments, repair a problem from real diagnostics, and run the checks that your task actually needs.

## What it is useful for

- Turn a module requirement into readable, synthesizable Verilog-2001 RTL.
- Explain an existing module through its ports, clock/reset behavior, state, timing, and data flow.
- Add or improve semantic comments without silently changing RTL behavior.
- Repair an existing design against a compiler, linter, simulator, or tool log.
- Prepare a testbench scaffold or request real simulation results when the environment is available.

The skill keeps results honest: a static review, a simulation, a synthesis run, and a hardware result are reported separately. A check that did not run is never presented as a completed result.

## Install

> Ask AI to install the skill from https://github.com/Eriemon/verilog-generator.

After installation, call the skill in the same conversation with `$readable-verilog-generator` or describe the Verilog task directly.

## What to prepare

Prepare only the material that matches your task:

![What to prepare for a Verilog task](assets/readme/project-facts.png)

- **New module:** purpose, module name, ports, clock and reset semantics, cycle-by-cycle behavior, protocol rules, boundary cases, and the checks you expect.
- **Existing RTL review:** the `.v` files, relevant testbench or integration context, and the questions or risks you want reviewed.
- **Commenting or repair:** the original RTL, the diagnostic or log, the intended behavior, and whether the change must be comment-only or may change logic.
- **Simulation or external validation:** a usable testbench with expected outcomes, the simulator/tool environment, and any required project or remote access.

If something important is missing, the skill asks for it before making a consequential change.

## How to call it

Use a normal request. These examples are ready to adapt:

```text
Use $readable-verilog-generator to create a Verilog-2001 AXI-Stream packet counter.
First ask for any missing interface and timing details, then show me the module spec
and WaveDrom behavior preview before generating RTL.
```

```text
Use $readable-verilog-generator to review this RTL for reset behavior, handshake
errors, width issues, naming, comments, and synthesis risks. Do not modify the file.
```

```text
Use $readable-verilog-generator to add semantic comments to this Verilog file.
Preserve every RTL token and show the comment-only verification result.
```

```text
Use $readable-verilog-generator to diagnose and repair this module from the
attached simulator log. Show the proposed change and validation results first.
```

## Preview and confirm before generation

For a new module or a behavior-changing repair, ask for a preview before asking AI to write the final RTL. The preview should make these decisions visible:

![Preview and confirmation before generation](assets/readme/design-profile.png)

1. Interface, port directions, widths, clock, and reset behavior.
2. State transitions, handshake conditions, data-path timing, and corner cases.
3. At least one WaveDrom scenario that shows the intended cycle behavior.
4. The checks that will be run and what each result can prove.

Confirm the preview only after the behavior matches your intent. If it does not, revise the requirement and preview again; do not treat an unconfirmed interpretation as the final design.

## What you receive

Depending on the request, the handoff can include:

![What you receive after the task](assets/readme/rule-rendering.png)

- readable Verilog-2001 source (`.v`);
- a same-name module specification (`<module>_spec.md`);
- WaveDrom behavior files (`.json5` and rendered `.svg`);
- a testbench scaffold or updated testbench when requested;
- review, repair, static-check, simulation, or tool reports showing only checks that actually ran;
- a concise explanation of remaining assumptions, warnings, and unverified areas.

## Example result

The example below is a 32-bit AXI-Stream meter. It counts bytes only after a `TVALID`/`TREADY` transfer, counts packets on a transferred `TLAST`, and saturates instead of wrapping.

```verilog
// AXI-Stream transaction detection
assign flag_transfer = i_axis_tvalid & i_axis_tready;
assign flag_packet_end = flag_transfer & i_axis_tlast;

always@(posedge i_axis_aclk or negedge i_axis_arstn)begin
	if(i_axis_arstn == 1'b0)begin
		cnt_packet_o <= 32'd0;
	end else if(i_clear == 1'b1)begin
		cnt_packet_o <= 32'd0;
	end else if(flag_packet_end == 1'b1)begin
		if(data_frame_sum[32] == 1'b0)begin
			cnt_packet_o <= data_frame_sum[31:0];
		end else begin
			cnt_packet_o <= 32'hFFFF_FFFF;
		end
	end
end
```

**[Open the complete commented source →](assets/examples/readme/axis_packet_meter.v)**

| Example check | Result |
| --- | ---: |
| Formatter AST | `PASS` |
| Readability, comments, naming, profile | `PASS` |
| Errors | `0` |
| Strict warnings | `0` |

> This is an example static result for the published sample. It does not claim that a simulator, synthesis tool, hardware target, or remote environment ran.

## Authors and citation

Jiyuan Liu and He Li · Southeast University（东南大学）· HIQC (Heterogeneous Intelligence and Quantum Computing Laboratory)

If this skill supports your research, teaching, or engineering work, cite [CITATION.cff](CITATION.cff).

```bibtex
@software{liu_2026_verilog_generator,
  author       = {Jiyuan Liu and He Li},
  title        = {{Verilog Generator}: An Agent Skill for Verilog-2001 RTL Workflows},
  year         = {2026},
  version      = {1.2.0},
  date         = {2026-08-12},
  url          = {https://github.com/Eriemon/verilog-generator},
  license      = {Apache-2.0},
  note         = {Agent skill for readable Verilog-2001 RTL workflows}
}
```

---

<p align="center">
  <a href="SKILL.md">Skill details</a>
  <span>&nbsp;&bull;&nbsp;</span>
  <a href="LICENSE">Apache License 2.0 (Apache-2.0)</a>
  <span>&nbsp;&bull;&nbsp;</span>
  <a href="CITATION.cff">Citation</a>
  <span>&nbsp;&bull;&nbsp;</span>
  <a href="mailto:<REDACTED_EMAIL>">Contact</a>
</p>
