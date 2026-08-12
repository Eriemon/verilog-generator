# AMD-Xilinx Primitive Semantic and Exemption Catalog

This document defines the static VG boundary for Xilinx primitives. An exemption means only that the scanner has governed facts for the exact primitive name, port direction, static width, clock role, and output boundary. It never removes an instance from every rule and never replaces Vivado elaboration, synthesis, implementation, simulation, or board evidence.

## 1. Scope and precedence

The semantic asset is `assets/xilinx_primitive_semantics.json`, loaded and validated by `load_primitive_semantic_catalog()`. Names must match the Verilog instance reference exactly. Case changes, aliases, prefixes, and fuzzy matches are not expanded automatically.

Source precedence is:

1. A real module definition in the target RTL.
2. An explicit reviewed external-interface stub or project-IP manifest.
3. The built-in UNISIM or XPM profile described by this document.
4. An unknown, conflicting, or incomplete source remains `inconclusive`.

A real RTL definition wins over a same-name built-in profile. A built-in profile and an explicit stub with different directions or widths do not compete by preference; the affected instance is reported as `inconclusive`. A project-wide allow-all switch is forbidden. Broad patterns such as `xpm_*` and `*_example` are not waivers.

## 2. Complete catalog

The current catalog contains 49 UNISIM primitives, 19 XPM primitives or macro modules, and 10 project-IP manifest categories. `validated_vivado_versions` is 2022.2 and 2023.2. Validation parts are `xc7a35tcpg236-1`, `xcku040-ffva1156-2-e`, and `xcu55c-fsvh2892-2L-e`. These facts define this governed scope; unlisted devices or versions do not receive an automatic exemption. Other toolchain projects still require their own compile and simulation evidence.

### 2.1 UNISIM: 49 exact names

`P1_FULL` means direction and scalar width are usable by VG097, and declared clock roles and output boundaries are usable by VG132, VG146, and VG147. `P2_BOUNDARY` means the interface is trusted but the internal implementation is modeled only at its declared boundary. `P3_OPAQUE` means the name is recognized but the available interface or internal semantics are insufficient for a pass.

| Category | Exact primitive names | Support level and boundary |
| --- | --- | --- |
| Differential and single-ended input buffers | `IBUF`, `IBUFDS` | `P1_FULL`; `I` and `IB` may carry clock identity, and `O` is a transparent output. |
| High-speed differential inputs | `IBUFDS_GTE2`, `IBUFDS_GTE3`, `IBUFDS_GTE4` | `P2_BOUNDARY`; `I` and `IB` are clock inputs, `CEB` is control, and `O` and `ODIV2` are clock outputs. GT internals are not expanded. |
| Single-ended outputs | `OBUF`, `OBUFT` | `P1_FULL`; `I` to `O` is transparent, and `OBUFT.T` is tri-state control rather than a clock pin. |
| Differential outputs | `OBUFDS`, `OBUFTDS` | `P1_FULL`; `I` to `O` and `OB` is transparent, with tri-state control kept at the data boundary. |
| Bidirectional I/O | `IOBUF`, `IOBUFDS` | `P2_BOUNDARY`; `O` may propagate transparently, while `IO` and `IOB` remain resolved `inout` boundaries without invented reverse ownership. |
| Global and regional clock buffers | `BUFG`, `BUFGCE`, `BUFGCTRL`, `BUFH`, `BUFHCE`, `BUFIO`, `BUFR` | `P1_FULL`; `I`, `I0`, `I1`, and `O` carry clock roles, control ports remain controls, and the clock output is a `clock_source` boundary. |
| Clock management | `MMCME2_ADV`, `PLLE2_ADV`, `MMCME3_ADV`, `PLLE3_ADV`, `MMCME4_ADV`, `PLLE4_ADV` | `P2_BOUNDARY`; `CLKIN*` and `CLKFBIN` are clock inputs, `CLKOUT*` and `CLKFBOUT` are clock-source outputs, and `LOCKED` is an opaque status boundary. Multiplication and phase are not guessed. |
| Flip-flops and latches | `FDRE`, `FDSE`, `FDCE`, `FDPE`, `LDCE`, `LDPE` | `P2_BOUNDARY`; `Q` is a `state_cut`, while `D`, enable, reset, and control remain independent input facts. Upstream logic is not expanded through the primitive. |
| On-chip RAM | `RAMB18E1`, `RAMB36E1`, `RAMB18E2`, `RAMB36E2` | `P3_OPAQUE`; the catalog does not fabricate port widths, read data, initialization, collision behavior, or internal cones. Use a project manifest for an exact interface. |
| FIFO | `FIFO18E1`, `FIFO36E1`, `FIFO18E2`, `FIFO36E2` | `P3_OPAQUE`; storage, data and status ports, read/write clocks, and reset behavior are not expanded. Use a project manifest for an exact interface. |
| DSP | `DSP48E1`, `DSP48E2` | `P3_OPAQUE`; arithmetic ports and pipeline stages are not guessed. Unknown control cones remain locally `inconclusive` until a manifest supplies boundaries. |
| Startup and configuration ports | `STARTUPE2`, `STARTUPE3`, `ICAPE2`, `ICAPE3`, `DNA_PORT`, `USR_ACCESSE2` | `P3_OPAQUE`; only the exact name and configuration boundary are recognized. Configuration and user-access ports require a project manifest. |
| 7-series and UltraScale GT channels | `GTXE2_CHANNEL`, `GTHE3_CHANNEL`, `GTYE4_CHANNEL` | `P3_OPAQUE`; transceiver internals, protocols, and reference-clock constraints are not inferred by a static Verilog scanner. |

The names above total 49: 5 input buffers, 4 single or differential outputs, 2 bidirectional I/O primitives, 7 clock buffers, 6 clock-management primitives, 6 flip-flop or latch primitives, 8 RAM or FIFO primitives, 2 DSP primitives, 6 startup or configuration primitives, and 3 GT channels.

### 2.2 XPM: 19 exact names

The `xpm_` prefix is not an exemption. Only the exact names below enter the catalog. CDC clock roles may support VG132; CDC, FIFO, and memory internals are not fabricated as recursive RTL.

| Category | Exact primitive names | Support level and boundary |
| --- | --- | --- |
| CDC | `xpm_cdc_array_single`, `xpm_cdc_async_rst`, `xpm_cdc_gray`, `xpm_cdc_handshake`, `xpm_cdc_low_latency_handshake`, `xpm_cdc_pulse`, `xpm_cdc_single`, `xpm_cdc_sync_rst` | `P2_BOUNDARY`; source and destination clocks, reset, send/request, or valid/ready roles are profile facts. Width parameters remain metadata; overrides and unknown expressions remain `inconclusive`. State outputs are `state_cut` boundaries and pulse outputs are transparent boundaries. |
| FIFO | `xpm_fifo_async`, `xpm_fifo_axif`, `xpm_fifo_axil`, `xpm_fifo_axis`, `xpm_fifo_sync` | `P3_OPAQUE`; FIFO storage, handshake, almost-full or empty, and protocol adaptation are not recursively expanded. Use a project manifest when the project interface is needed. |
| Memory | `xpm_memory_dpdistram`, `xpm_memory_dprom`, `xpm_memory_sdpram`, `xpm_memory_spram`, `xpm_memory_sprom`, `xpm_memory_tdpram` | `P3_OPAQUE`; data width, depth, read latency, initialization, and collision policy are not inferred from the name. |

The XPM list totals 8 CDC, 5 FIFO, and 6 memory profiles. An unknown `xpm_*` name, user wrapper, or parameterized generated module still needs real RTL or an external-interface manifest.

### 2.3 Project IP: 10 manifest categories

These are not UNISIM or XPM primitives and are not a global module allowlist. They are governed categories whose instance names must be filled from project-generated evidence:

`vio`, `ila`, `clock_wizard`, `processor_system_reset`, `block_memory_generator`, `fifo_generator`, `axi_memory_mapped`, `axi_stream`, `gt_phy`, and `generic_ip`.

Project IP defaults to `P4_MANIFEST_ONLY`. A manifest may provide exact directions, widths, clock and reset roles, and output boundaries, but a category name alone never passes. Without a manifest, VG097, VG132, VG146, and VG147 remain unknown or not applicable according to fact completeness.

## 3. Consumption by the four related VG gates

### VG097: instance ports and connection widths

The profile projects an existing module interface: port names, `input`/`output`/`inout` direction, and static width. Named connections compare formal and actual ports; positional connections, parameter overrides, dynamic expressions, or missing facts remain `inconclusive`. For `IBUFDS`, `I`, `IB`, and `O` are scalar width 1. A real same-name RTL definition wins, and a conflicting external stub cannot silently replace it.

### VG132: clocks only enter clock ports

The rule first establishes module-local clock identity from sequential `always` blocks, then uses the profile's explicit clock ports. `IBUFDS.I`, `IBUFDS.IB`, `BUFG.I`, and the clock ports of `xpm_cdc_*` are legal clock boundaries. `CE`, `T`, reset, data, and ordinary `inout` ports are not allowed merely because their names look similar. Complex connections, profile conflicts, and incomplete instances remain fail-closed.

### VG146 and VG147: complete source closure and output boundaries

Primitive profiles do not invent internal RTL expressions. `transparent` and `clock_source` outputs may propagate to the parent cone. `state_cut` truncates upstream ownership at Q or a storage output, while D, enable, and reset remain separately checked. `multi_driver` and `inout` boundaries do not invent a unique reverse driver. `opaque` makes only the affected output target `inconclusive`; it does not erase other findings. VG146 handles acyclic closure, and VG147 handles loops or loop-bound evidence. P3 and P4 facts cannot be reported as pass when the required boundary facts are missing.

## 4. Exemption versus hardware evidence

- An exemption supplies static interface and boundary facts only; it does not prove the primitive's internal function.
- Vivado or XSim, synthesis, implementation, timing, bitstream, board execution, and primitive simulation-library evidence remain separate records.
- Add only exact-name profiles. Do not whitelist `UNISIM.*`, `xpm_*`, `*_ip`, or an entire library directory with a wildcard.
- Unknown names, incomplete fields, explicit-stub conflicts, indeterminate parameter ports, and same-name RTL conflicts remain locally `inconclusive`.
- Unlisted Versal or NoC, SecureIP, SIMPRIM, UNIMACRO, third-party IP, or custom wrappers require a reviewed external-interface source or manifest with tool version, device, and port provenance.

## 5. Auditable entry points

- Catalog asset: `assets/xilinx_primitive_semantics.json`
- Catalog loader: `scripts/python/workflow/primitive_catalog.py::load_primitive_semantic_catalog`
- Primitive facts: `scripts/python/quality/vg_primitive_facts.py::load_primitive_facts`
- Conflict resolver: `scripts/python/quality/vg_primitive_facts.py::resolve_primitive_profile`
- Clock-role query: `scripts/python/quality/vg_primitive_facts.py::primitive_port_is_clock_role`
- Output-boundary query: `scripts/python/quality/vg_primitive_facts.py::primitive_output_boundary`
- Unified entry point: `scripts/python/quality/vg_semantic_engine.py::run_vg_semantic_gate`

These entry points accept the complete catalog or a resolver-produced profile. Unknown names and conflicts retain their reason and location in the report; a project-wide exemption must not conceal them.
