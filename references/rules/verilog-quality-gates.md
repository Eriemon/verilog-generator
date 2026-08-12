# Unified Verilog Quality Gates

This reference is the stable, install-safe view of the authoritative catalog in `assets/verilog_quality_gates.json`.

## Table of contents

- [Contract](#contract)
- [Catalog](#catalog)

## Contract

- Public entry: `run_verilog_quality_gate(...)`
- Report schema: v2
- Report fields: `vg_catalog_version`, `vg_rule_summary`, and `vg_rule_results`
- Catalog size: 125 active rules and 0 reserved rules
- Combinational operation budget: `config.max_combinational_operations_per_target` is a required positive integer; the shipped value is `3`
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
- VG146 and VG147 count distinct reachable hardware operation occurrences for each static target endpoint, not source names and not only the longest path. Unary, binary, comparison, shift, arithmetic, reduction, ternary, dynamic selection, and branch/decode selection operations count once per RTL occurrence; assignment, constants, concatenation, replication, and constant selection do not. Runtime-reachable mutually exclusive branches are unioned. Reaching the same upstream producer by multiple paths counts that producer once for the current endpoint.
- VG146 owns targets whose cone has no operation cloned from a procedural `for`. VG147 owns targets containing at least one cloned loop operation, including downstream endpoints that reach that loop. A constant zero-iteration loop contributes no operation; each supported constant-bound iteration otherwise clones the body operation occurrences, including nested Cartesian iteration tuples. Unknown or parameter-dependent bounds, dynamic loop lvalues, and generate-loop hierarchy shapes remain fail-closed for the affected target instead of passing on a partial count.
- A register or latch Q output cuts upstream expansion, but every register or latch D input and enable expression remains a checked endpoint. Therefore moving an over-budget expression directly into `always @(posedge clk)` does not bypass VG146. Canonical asynchronous reset control is excluded from the data-cone count; unsupported reset, driver, lvalue, function, hierarchy, or recursion shapes remain fail-closed for the affected endpoint.
- Over-budget remediation must prefer pipeline registers, registered flags or predecode, and multi-cycle FSM steps. These changes can alter visible latency. If the protocol latency is immutable, the gate remains blocking and requires manual architecture review; rewriting the same chain into another combinational syntax is not a valid waiver.

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
