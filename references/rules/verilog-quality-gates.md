# Unified Verilog Quality Gates

This reference is the stable, install-safe view of the authoritative catalog in `assets/verilog_quality_gates.json`.

## Table of contents

- [Contract](#contract)
- [Catalog](#catalog)

## Contract

- Public entry: `run_verilog_quality_gate(...)`
- Report schema: v3
- Report fields: `version`, `vg_catalog_version`, `vg_rule_summary`, `vg_rule_results`, and actionable `findings`
- Catalog size: 136 active rules and 0 reserved rules; catalog version 9
- Every applicable active non-passed rule emits at least one actionable finding.
- Each finding exposes `rule_id`, `status`, `severity`, structured `location` and `evidence`, a concrete `problem`, and `guidance` containing the change instruction, ordered steps, risk, human-review obligation, and a non-empty `examples` list of bad/good objects.
- Exact source locations use confirmed `file` plus one-based `line_start`/`line_end` under `scope=file`; aggregate or unknown locations use `scope=file`, `scope=cross_file`, or `scope=run` without a fabricated line number and include a boundary `note`.
- Combinational operation budget: `config.max_combinational_operations_per_target` is a required positive integer; the shipped value is `3`
- Packed dynamic lookup budget: `config.packed_dynamic_lookup_block_bits` is a required positive integer; the shipped value is `1024`
- Severity policy: BLOCKER non-pass results always block; WARNING non-pass results block strict runs

## Catalog

| Gate | Level | Rule key | Status |
| --- | --- | --- | --- |
| VG000 | WARNING | existing_vg000 | active |
| VG001 | WARNING | existing_vg001 | active |
| VG002 | WARNING | existing_vg002 | active |
| VG003 | WARNING | existing_vg003 | active |
| VG004 | WARNING | existing_vg004 | active |
| VG005 | WARNING | existing_vg005 | active |
| VG006 | WARNING | existing_vg006 | active |
| VG007 | WARNING | existing_vg007 | active |
| VG008 | WARNING | existing_vg008 | active |
| VG009 | WARNING | existing_vg009 | active |
| VG010 | WARNING | existing_vg010 | active |
| VG011 | WARNING | existing_vg011 | active |
| VG012 | WARNING | existing_vg012 | active |
| VG013 | WARNING | existing_vg013 | active |
| VG014 | WARNING | existing_vg014 | active |
| VG015 | WARNING | existing_vg015 | active |
| VG020 | WARNING | existing_vg020 | active |
| VG021 | WARNING | existing_vg021 | active |
| VG022 | WARNING | existing_vg022 | active |
| VG023 | WARNING | existing_vg023 | active |
| VG024 | WARNING | existing_vg024 | active |
| VG025 | WARNING | existing_vg025 | active |
| VG030 | WARNING | existing_vg030 | active |
| VG031 | WARNING | existing_vg031 | active |
| VG040 | WARNING | existing_vg040 | active |
| VG041 | WARNING | existing_vg041 | active |
| VG042 | WARNING | existing_vg042 | active |
| VG050 | WARNING | existing_vg050 | active |
| VG051 | WARNING | existing_vg051 | active |
| VG052 | WARNING | existing_vg052 | active |
| VG053 | WARNING | existing_vg053 | active |
| VG054 | WARNING | existing_vg054 | active |
| VG055 | WARNING | existing_vg055 | active |
| VG056 | WARNING | existing_vg056 | active |
| VG057 | WARNING | existing_vg057 | active |
| VG058 | WARNING | existing_vg058 | active |
| VG059 | WARNING | existing_vg059 | active |
| VG060 | WARNING | existing_vg060 | active |
| VG061 | WARNING | existing_vg061 | active |
| VG062 | WARNING | existing_vg062 | active |
| VG063 | WARNING | existing_vg063 | active |
| VG064 | WARNING | existing_vg064 | active |
| VG065 | WARNING | existing_vg065 | active |
| VG066 | WARNING | existing_vg066 | active |
| VG067 | WARNING | existing_vg067 | active |
| VG068 | WARNING | existing_vg068 | active |
| VG069 | WARNING | existing_vg069 | active |
| VG070 | WARNING | existing_vg070 | active |
| VG071 | WARNING | existing_vg071 | active |
| VG072 | BLOCKER | op_no_xz_arith | active |
| VG073 | WARNING | clk_single_domain | active |
| VG074 | WARNING | op_no_logic_on_vector | active |
| VG075 | BLOCKER | rst_no_sync_async_mix | active |
| VG076 | WARNING | case_no_overlap | active |
| VG077 | WARNING | rst_one_signal_per_always | active |
| VG078 | BLOCKER | op_no_xz_condition | active |
| VG079 | BLOCKER | comb_blocking_assign | active |
| VG080 | BLOCKER | loop_no_nonindex_arith | active |
| VG081 | WARNING | repeat_const_count | active |
| VG082 | BLOCKER | rst_no_glitchy_comb | active |
| VG083 | BLOCKER | loop_no_reset_logic_mix | active |
| VG084 | BLOCKER | case_item_in_range_width | active |
| VG085 | BLOCKER | op_rel_width_match | active |
| VG086 | BLOCKER | fsm_has_initial_state | active |
| VG087 | BLOCKER | synth_no_reset_override | active |
| VG088 | WARNING | branch_cond_scalar | active |
| VG089 | BLOCKER | clk_no_comb_clock | active |
| VG090 | BLOCKER | case_control_not_constant | active |
| VG091 | WARNING | array_index_simple | active |
| VG092 | BLOCKER | loop_at_least_once | active |
| VG093 | BLOCKER | rst_no_async_to_data_pin | active |
| VG094 | BLOCKER | fsm_default_reset_regs | active |
| VG095 | BLOCKER | latch_no_gate_primitive | active |
| VG096 | WARNING | clk_avoid_gating | active |
| VG097 | BLOCKER | conn_port_width_match | active |
| VG098 | BLOCKER | fsm_no_dead_unreachable | active |
| VG099 | WARNING | rst_dedicated_generator | active |
| VG100 | WARNING | rst_no_internal_async_src | active |
| VG101 | BLOCKER | branch_cond_no_xz | active |
| VG102 | WARNING | rst_no_set_reset_pair | active |
| VG103 | BLOCKER | assign_no_dup_condition | active |
| VG104 | BLOCKER | latch_no_comb_loop | active |
| VG105 | BLOCKER | case_item_constant_only | active |
| VG106 | BLOCKER | func_no_recursion | active |
| VG107 | BLOCKER | clk_no_regout_clock | active |
| VG108 | WARNING | case_default_not_xz | active |
| VG109 | WARNING | comb_if_has_else | active |
| VG110 | BLOCKER | ff_init_on_reset | active |
| VG111 | BLOCKER | case_has_default | active |
| VG112 | WARNING | fsm_limit_state_count | active |
| VG113 | BLOCKER | ff_no_mixed_reset_style | active |
| VG114 | WARNING | synth_no_full_case_attr | active |
| VG115 | BLOCKER | task_io_width_match | active |
| VG116 | BLOCKER | ff_reset_condition_match | active |
| VG117 | BLOCKER | sens_no_or_separator | active |
| VG118 | WARNING | rst_no_logic_in_async_path | active |
| VG119 | WARNING | fsm_min_transition_flips | active |
| VG120 | WARNING | clk_single_edge | active |
| VG121 | BLOCKER | func_return_width | active |
| VG122 | BLOCKER | op_no_arith_overflow | active |
| VG123 | BLOCKER | loop_for_const_bounds | active |
| VG124 | BLOCKER | initial_forbidden | active |
| VG125 | BLOCKER | literal_width_match | active |
| VG126 | BLOCKER | sens_list_complete_minimal | active |
| VG127 | BLOCKER | subprogram_no_global_write | active |
| VG128 | WARNING | case_no_casex_casez | active |
| VG129 | BLOCKER | seq_nonblocking_assign | active |
| VG130 | BLOCKER | array_index_in_range | active |
| VG131 | BLOCKER | assign_no_delay | active |
| VG132 | BLOCKER | clk_only_clock_pin | active |
| VG133 | BLOCKER | task_no_timing_control | active |
| VG134 | BLOCKER | op_no_sign_mix | active |
| VG135 | WARNING | latch_separate_from_comb | active |
| VG136 | BLOCKER | comb_no_feedback | active |
| VG137 | BLOCKER | assign_width_match | active |
| VG138 | WARNING | literal_explicit_base_width | active |
| VG139 | BLOCKER | func_no_nonblocking | active |
| VG140 | BLOCKER | procedural_assign_to_wire | active |
| VG141 | BLOCKER | multiple_drivers | active |
| VG142 | BLOCKER | wire_declaration_inline_assignment | active |
| VG143 | BLOCKER | simulation_system_task_in_rtl | active |
| VG144 | BLOCKER | fsm_three_segment_procedural_next_state | active |
| VG145 | BLOCKER | comb_cone_max_three_sources | active |
| VG146 | BLOCKER | comb_operation_budget | active |
| VG147 | BLOCKER | for_comb_operation_budget | active |
| VG148 | BLOCKER | functional_verilog_filename | active |
| VG149 | BLOCKER | testbench_filename_prefix | active |
| VG150 | WARNING | comments.semantic_integrity | active |
| VG151 | BLOCKER | parameter_contract_coverage | active |
| VG152 | BLOCKER | packed_dynamic_lookup_resource | active |
| VG153 | BLOCKER | read_without_driver | active |
| VG154 | WARNING | unused_declaration | active |
| VG155 | BLOCKER | ready_valid_transfer_integrity | active |
| VG156 | BLOCKER | naming.declaration_digit_tokens | active |
| VG157 | BLOCKER | comments.version_markers | active |
| VG158 | BLOCKER | naming.functional_declaration | active |

## Recognition Scope Contracts

- Reset roles are recognized as complete underscore-delimited semantic segments. Names such as `rstn`, `rst_n`, `i_rstn`, `foo_rstn_sync`, `i_tb_rstn`, `i_axi_arstn`, `i_axis_arstn`, `i_ahb_hrstn`, and `i_apb_prstn` are supported; ordinary substrings such as `burst_count`, `forest`, `setup`, and `clearance` are not reset controls.
- VG073 and VG132 build clock identity independently for each module. Different clock port names across module boundaries do not by themselves prove multiple clock domains or clock-as-data use.
- VG012 requires module `parameter` names to use `C_` plus uppercase naming. Ordinary non-state `localparam` names must be uppercase without `C_`; state encodings continue to use `ST_`.
- VG081 and VG123 resolve `parameter` and `localparam` names only in the module that declares them. VG123 accepts deterministic Verilog-2001 sized binary, octal, decimal, and hexadecimal literals, while X, Z, question-mark, malformed, and undeclared-symbol expressions remain unsupported.
- VG078 forbids X/Z in conditions and general data selection. The only exception is a continuous open-drain `0/Z` assignment to an `inout` port declared directly by the same module. The RHS must be one simple, non-nested ternary whose condition contains no X/Z and whose data branches are exactly `1'b0` and `1'bz`, in either order.
- Shared width facts accept a parameter or integer endpoint with one optional decimal `+/-` delta and optional surrounding whitespace. Multiplication, compound arithmetic, malformed expressions, and unknown parameters remain unresolved.
- VG144 requires three independent FSM processes: a clocked current-state register, a procedural combinational next-state process, and a separate state-driven output/task process. `assign state_next = ...` and equivalent next-state aliases are BLOCKER findings.
- VG145 combines continuous assigns and combinational `always` assignments into one module-local transitive graph. Data expressions, conditions, comparisons, shifts, concatenations, selections, ternaries, and function arguments contribute dependencies. Different bit-selects or slices remain distinct sources; parameter/localparam/genvar/loop constants are excluded; sequential registers and instance outputs terminate expansion.
- VG145 allows at most three expanded runtime source references for every combinational target. Moving a chain into `always @(*)` does not bypass the gate. The only output exemption is an exact `assign <output> = <name>_o;` bridge where `<name>_o` is declared `reg` and driven by a clocked process; any output-side operation or multi-source expression remains checked.
- VG146 and VG147 count distinct reachable hardware operation occurrences for each static target endpoint across the complete source closure, not source names and not only the longest path. The formatter's structured named/positional associations are the only authority for binding instance actuals to child formals; the gate does not reparse instance text. Parameter overrides are evaluated in the parent environment, child parameters follow declaration order, localparams are recomputed, and the resulting specialization fingerprint selects a deterministic materialized implementation.
- Every source module definition is an independent definition root. A report identity is `(definition root, full instance path, specialization fingerprint, static target, gate id)`, so two occurrences of the same module are not merged. Output actuals expand through the known child implementation, including whole signals, constant bits/slices, and recursive static concatenations. Inout is a resolved boundary and never selects a reverse child owner. Ordinary wire/tri targets merge every known driver; one unknown driver makes only that target inconclusive.
- Unary, binary, comparison, shift, arithmetic, reduction, ternary, dynamic selection, and branch/decode selection operations count once per RTL occurrence; assignment, constants, concatenation, replication, and constant selection do not. Runtime-reachable mutually exclusive branches are unioned. Reaching the same upstream producer by multiple paths counts that producer once for the current endpoint.
- VG146 owns targets with `loop_presence=absent`; VG147 owns targets with `loop_presence=present`, including downstream endpoints that reach a cloned loop operation. A constant zero-iteration loop contributes no operation; each supported constant-bound iteration otherwise clones the body operation occurrences, including nested Cartesian iteration tuples. When `loop_presence=unknown`, both gates are applicable and emit the same root/path/specialization/target/count/reason evidence. If the known lower bound exceeds the limit both gates fail; otherwise both are inconclusive.
- A directly clocked child `output reg` or exact bridge to an internal registered Q cuts upstream expansion, but every register or latch D input and enable expression remains a checked endpoint. A combinational `output reg` continues to expand, and mixed storage/combinational driving is locally inconclusive. Therefore moving an over-budget expression directly into `always @(posedge clk)` does not bypass VG146. Canonical asynchronous reset control is excluded from the data-cone count; unsupported reset, driver, lvalue, function, hierarchy, or recursion shapes remain fail-closed for the affected endpoint.
- VG146/VG147 evidence names `definition_root`, `instance_path`, `specialization`, `target`, `child_output`, `operation_count`, `limit`, `inconclusive_reason`, and `loop_presence`. Remediation must be based on this complete identity and must not waive a failure by moving the same chain across module or procedural syntax.
- Over-budget remediation must prefer pipeline registers, registered flags or predecode, and multi-cycle FSM steps. These changes can alter visible latency. If the protocol latency is immutable, the gate remains blocking and requires manual architecture review; rewriting the same chain into another combinational syntax is not a valid waiver.
- VG148 applies independently to every readable `.v` and `.sv` file. It rejects only a terminal independent version or pure-number suffix: `_vN`, `-vN`, `_verN`, `-verN`, `_versionN`, `-versionN`, `_N`, or `-N`, case-insensitively. Digits embedded in functional identities such as `axi4_lite`, `crc32`, `i2c_master`, and `ad9361_if` remain valid.
- VG149 recognizes testbench role from a `tb_`, `_tb`, or `testbench` filename marker, an exact `tb`/`testbench`/`sim` directory segment, a user confirmation, or at least two independent content evidence groups. Every resolved testbench must use `tb_<function>`; `_tb` is evidence but never a naming waiver, so `counter_tb.v` is explicitly rejected.
- The VG149 content evidence groups are `initial_process`, `simulation_task`, `clock_stimulus`, and `dut_self_check`. Comments and strings are masked before evidence detection. An ordinary filename with at least two groups remains `inconclusive` and sets `confirmation_required` until `file_role_confirmations` resolves it. A `design` confirmation makes VG149 not applicable; a `testbench` confirmation still fails until the file is renamed with `tb_`.
- VG150 evaluates only formatter-bound entity comments. Configured workflow evidence markers such as graph, test, verification, review, and evidence-chain phrases fail closed on the entity line; evidence belongs in the gate report rather than RTL comments. It also rejects a high-confidence rotation family only when one entity label has at least eight candidates, four distinct fixed tails of length four to eight, at least four Chinese prefix characters, three changing positions, and two complete repeated cycles. A learned shorter tail is checked only after that family is established. Ordinary Chinese descriptions and isolated words do not fail solely because they contain a Chinese word.
- Unreadable `.v`/`.sv` input fails closed. VG148 and VG149 return `error`, and the aggregate public report preserves the same stable relative-path read error through `VG000/file.encoding` without duplicating the `.v` diagnostic.
- VG151 reads `design_requirements.parameter_constraints` as a top-level, module-independent contract list. Each expression is parsed in a restricted integer environment; its referenced parameter identifiers determine automatic applicability. `module`, `instance`, `hierarchy`, and `scope` fields are forbidden. A finding includes `constraint_id`, `required_parameters`, only the referenced `parameter_values`, `specialization_fingerprint`, `expression`, and a machine-readable `reason`.
- VG152 applies when a packed parameter, localparam-derived packed declaration, or packed signal is dynamically selected. Width is evaluated from the declaration and localparam environment; static slices, unpacked memories, and recognized memory/primitive structures remain outside the blocker. A dynamic packed store at or above `packed_dynamic_lookup_block_bits` fails with `width`, `threshold`, `selector`, `selector_expression`, `resource_class`, and remediation alternatives. The rule recommends a structured case/FSM, inferred memory, or vendor memory primitive rather than prescribing one ROM primitive.
- VG153 checks only read signals and output ports that lack a confirmable driver. It does not treat an unread, undriven declaration as a read-without-driver violation, and input/inout ports are boundary-driven facts.
- VG154 is a WARNING-level declaration liveness check covering parameters, localparams, reg/wire declarations, functions, and tasks. Subprogram bodies are not counted as top-level observable use; structured function-call facts and top-level references are the use evidence. Findings expose `symbol`, `declaration_kind`, and `use_state=unused`.
- VG155 consumes top-level `handshake_channels` or `interface_profile.handshake_channels` role facts without a module-name field. A channel must expose valid and ready ports (payload is optional evidence); each observed transfer consumer must be controlled by both roles. Findings retain the channel id plus valid, ready, payload, controls, and missing-role evidence.
- VG156 checks only variable declarations exposed by the formatter AST: module parameters, localparams, ports and internal declarations plus function/task formals and local declarations. After splitting an identifier on `_`, every token containing a digit must exactly match the case-insensitive allowlist `i2c`, `axi4`, `axi4lite`, `crc8`, `crc16`, `crc32`, `crc64`, `ddr2`, `ddr3`, `ddr4`, `ddr5`, `pcie3`, `pcie4`, `pcie5`, `sfp28`, `ad9361`, `sha1`, `sha2`, `sha256`, `md5`, `10g`, `25g`, `40g`, or `100g`. Sequence tokens such as `v1`, `w0`, `1`, and `2` fail.
- VG157 scans lexical line and block comments after masking string literals. Matching is case-insensitive and rejects ordinary-comment forms `vN`, `vN.N`, `verN`, `versionN`, and `version N`. The only exemption is the exact fixed bilingual header and revision-history prefix that the formatter itself recognized and stripped; user-authored comments elsewhere remain applicable.
- VG158 checks the same declaration set as VG156. It repeatedly strips `i_`, `o_`, `io_`, `C_`, `ST_`, `reg_`, `cnt_`, `state_`, `flag_`, `enc_`, and `dec_`, then repeatedly strips `_o`. At least one remaining underscore-delimited token must not belong to the configured meaningless-token set. Unknown domain terms pass by default, so the rule rejects only a closed list of obvious placeholders or generic type words. `data` is functional; `data_1` passes VG158 but fails VG156.
- If a function/task declaration shape prevents complete recovery of applicable formals or local declarations, VG156/VG158 preserve any definite violations; otherwise they return `inconclusive` rather than fabricating a pass or line 1 location.

## Formerly Reserved Rules

### VG080 - Loop arithmetic ownership

- Procedural for-header updates to non-index variables are BLOCKER findings.
- Arithmetic datapath writes inside a procedural loop are WARNING findings.
- Loop index updates, address or bit selection, and generate-for constructs are excluded.

### VG082 - Reset source integrity

- A reset produced by local combinational logic fails.
- An instance output used as reset is inconclusive unless the spec names an exact trusted module and output port.
- An exact `spec.reset.trusted_external_sources` match passes.

### VG099 - Dedicated reset generator

- Inline reset generation in a functional module fails.
- A recognizable dedicated reset generator or reset controller module passes.

### VG119 - FSM transition bit flips

- Binary state transitions may flip at most one bit.
- Recognized one-hot transitions may flip at most two bits.
- If no complete FSM transition structure can be extracted, the result is passed with `applicable=false`.

### VG125 - Literal declared width

- Binary, octal, and hexadecimal digits preserve representation width.
- Decimal literals use the minimum value width.
- X, Z, and question-mark literals are skipped as ambiguous.
- Parameter and localparam declarations, plus ordinary zero clear literals, are exempt.
