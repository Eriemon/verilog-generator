# Fixed RTL PG Gate Catalog

This page is the ASCII-safe human reference for `assets/rtl_md_constraints.json`.
The machine contract reads the explicit `gate_id` stored in the catalog. Gate IDs
must never be inferred from list order.

- Fixed range: PG1001 through PG1072.
- Total gates: 72.
- Active gates: 67, including 48 BLOCKER and 19 WARNING gates.
- Reserved gates: 22. They remain prompt guidance and never count as passed.
- An active BLOCKER passes only with `passed`. The `failed`, `inconclusive`,
  `error`, and `not_run` states fail closed.
- An active WARNING uses the same blocking policy in strict delivery and is
  diagnostic only in non-strict delivery.
- Fixed result states: `passed`, `failed`, `inconclusive`, `error`, `not_run`,
  and `reserved`.

| Gate | Status | Level | Stable rule key |
|---|---|---|---|
| PG1001 | active | BLOCKER | op_no_xz_arith |
| PG1002 | active | WARNING | clk_single_domain |
| PG1003 | active | WARNING | op_no_logic_on_vector |
| PG1004 | active | BLOCKER | rst_no_sync_async_mix |
| PG1005 | active | WARNING | case_no_overlap |
| PG1006 | active | WARNING | rst_one_signal_per_always |
| PG1007 | active | BLOCKER | op_no_xz_condition |
| PG1008 | active | BLOCKER | comb_blocking_assign |
| PG1009 | reserved | BLOCKER | loop_no_nonindex_arith |
| PG1010 | active | WARNING | repeat_const_count |
| PG1011 | reserved | BLOCKER | rst_no_glitchy_comb |
| PG1012 | active | BLOCKER | loop_no_reset_logic_mix |
| PG1013 | active | BLOCKER | case_item_in_range_width |
| PG1014 | active | BLOCKER | op_rel_width_match |
| PG1015 | active | BLOCKER | fsm_has_initial_state |
| PG1016 | active | BLOCKER | synth_no_reset_override |
| PG1017 | active | WARNING | branch_cond_scalar |
| PG1018 | active | BLOCKER | clk_no_comb_clock |
| PG1019 | active | BLOCKER | case_control_not_constant |
| PG1020 | active | WARNING | array_index_simple |
| PG1021 | active | BLOCKER | loop_at_least_once |
| PG1022 | active | BLOCKER | rst_no_async_to_data_pin |
| PG1023 | active | BLOCKER | fsm_default_reset_regs |
| PG1024 | active | BLOCKER | latch_no_gate_primitive |
| PG1025 | active | WARNING | clk_avoid_gating |
| PG1026 | active | BLOCKER | conn_port_width_match |
| PG1027 | active | BLOCKER | fsm_no_dead_unreachable |
| PG1028 | reserved | WARNING | rst_dedicated_generator |
| PG1029 | active | WARNING | rst_no_internal_async_src |
| PG1030 | active | BLOCKER | branch_cond_no_xz |
| PG1031 | active | WARNING | rst_no_set_reset_pair |
| PG1032 | active | BLOCKER | assign_no_dup_condition |
| PG1033 | active | BLOCKER | latch_no_comb_loop |
| PG1034 | active | BLOCKER | case_item_constant_only |
| PG1035 | active | BLOCKER | func_no_recursion |
| PG1036 | active | BLOCKER | clk_no_regout_clock |
| PG1037 | active | WARNING | case_default_not_xz |
| PG1038 | active | WARNING | comb_if_has_else |
| PG1039 | active | BLOCKER | ff_init_on_reset |
| PG1040 | active | BLOCKER | case_has_default |
| PG1041 | active | WARNING | fsm_limit_state_count |
| PG1042 | active | BLOCKER | ff_no_mixed_reset_style |
| PG1043 | active | WARNING | synth_no_full_case_attr |
| PG1044 | active | BLOCKER | task_io_width_match |
| PG1045 | active | BLOCKER | ff_reset_condition_match |
| PG1046 | active | BLOCKER | sens_no_or_separator |
| PG1047 | active | WARNING | rst_no_logic_in_async_path |
| PG1048 | reserved | WARNING | fsm_min_transition_flips |
| PG1049 | active | WARNING | clk_single_edge |
| PG1050 | active | BLOCKER | func_return_width |
| PG1051 | active | BLOCKER | op_no_arith_overflow |
| PG1052 | active | BLOCKER | loop_for_const_bounds |
| PG1053 | active | BLOCKER | initial_forbidden |
| PG1054 | reserved | BLOCKER | literal_width_match |
| PG1055 | active | BLOCKER | sens_list_complete_minimal |
| PG1056 | active | BLOCKER | subprogram_no_global_write |
| PG1057 | active | WARNING | case_no_casex_casez |
| PG1058 | active | BLOCKER | seq_nonblocking_assign |
| PG1059 | active | BLOCKER | array_index_in_range |
| PG1060 | active | BLOCKER | assign_no_delay |
| PG1061 | active | BLOCKER | clk_only_clock_pin |
| PG1062 | active | BLOCKER | task_no_timing_control |
| PG1063 | active | BLOCKER | op_no_sign_mix |
| PG1064 | active | WARNING | latch_separate_from_comb |
| PG1065 | active | BLOCKER | comb_no_feedback |
| PG1066 | active | BLOCKER | assign_width_match |
| PG1067 | active | WARNING | literal_explicit_base_width |
| PG1068 | active | BLOCKER | func_no_nonblocking |
| PG1069 | active | BLOCKER | procedural_assign_to_wire |
| PG1070 | active | BLOCKER | multiple_drivers |
| PG1071 | active | BLOCKER | wire_declaration_inline_assignment |
| PG1072 | active | BLOCKER | simulation_system_task_in_rtl |

## Execution Contract

The public entry point is
`run_rtl_pg_gate(root, spec=None, strict=True, include_testbench=False)`.
Deliverable report v2 keeps all 72 entries in `pg_gate_results` and aggregates
active, reserved, and result-state counts in `pg_gate_summary`. Formatter AST is
the only parsing authority. Lexical scans may run only inside formatter-confirmed
module or subprogram boundaries.
