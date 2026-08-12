# RTL-MD Constraint Catalog

This file is the stable RTL-MD constraint reference for erie-verilog-generator.
It lists every absorbed rule by semantic ID and execution layer. The durable
skill package does not depend on temporary development inputs.

## Table of Contents / Topic Index

- [Package Metadata](#package-metadata)
- [Enforcement Model](#enforcement-model)
- [Coverage By Topic](#coverage-by-topic)
- [Full Rule List](#full-rule-list)
- [Rule Summaries](#rule-summaries)
- [Implementation Notes](#implementation-notes)

## Package Metadata

| Field | Value |
| --- | --- |
| total_rules | 68 |
| required_rules | 47 |
| advisory_rules | 21 |
| shuffle_seed | 20260609 |
| semantic_rule_names | true |

## Enforcement Model

| Layer | Meaning |
| --- | --- |
| automated_error | High-confidence static check; any hit blocks final RTL. |
| automated_warning | High-confidence static warning; follow by default or record a deviation reason. |
| review_error | Blocking MUST rule checked by prompt review, manifest evidence, simulation, synthesis, or human review. |
| prompt_warning | REC rule carried through prompts; follow by default or record a deviation reason. |

MUST rules are blocking error constraints. REC rules are warning-level defaults.
Teaching counterexamples may be used only as review material and must not be copied
into final RTL artifacts.

## Coverage By Topic

| Topic | Count | Rule IDs |
| --- | ---: | --- |
| case-items | 7 | REC_CASE_NO_OVERLAP, MUST_CASE_ITEM_IN_RANGE_WIDTH, MUST_CASE_CONTROL_NOT_CONSTANT, MUST_CASE_ITEM_CONSTANT_ONLY, REC_CASE_DEFAULT_NOT_XZ, MUST_CASE_HAS_DEFAULT, REC_CASE_NO_CASEX_CASEZ |
| clock-reset | 12 | REC_CLK_SINGLE_DOMAIN, MUST_LOOP_NO_RESET_LOGIC_MIX, MUST_SYNTH_NO_RESET_OVERRIDE, MUST_FSM_DEFAULT_RESET_REGS, REC_CLK_AVOID_GATING, REC_RST_NO_SET_RESET_PAIR, MUST_CLK_NO_REGOUT_CLOCK, MUST_FF_INIT_ON_RESET, MUST_FF_NO_MIXED_RESET_STYLE, MUST_FF_RESET_CONDITION_MATCH, REC_CLK_SINGLE_EDGE, MUST_CLK_ONLY_CLOCK_PIN |
| design-review | 7 | MUST_RST_NO_SYNC_ASYNC_MIX, MUST_RST_NO_ASYNC_TO_DATA_PIN, MUST_FSM_NO_DEAD_UNREACHABLE, REC_RST_NO_INTERNAL_ASYNC_SRC, REC_FSM_LIMIT_STATE_COUNT, REC_RST_NO_LOGIC_IN_ASYNC_PATH, REC_FSM_MIN_TRANSITION_FLIPS |
| general-review | 5 | REC_RST_ONE_SIGNAL_PER_ALWAYS, REC_REPEAT_CONST_COUNT, REC_RST_DEDICATED_GENERATOR, REC_SYNTH_NO_FULL_CASE_ATTR, MUST_SUBPROGRAM_NO_GLOBAL_WRITE |
| operators-expressions | 14 | MUST_OP_NO_XZ_ARITH, REC_OP_NO_LOGIC_ON_VECTOR, MUST_OP_NO_XZ_CONDITION, MUST_LOOP_NO_NONINDEX_ARITH, MUST_OP_REL_WIDTH_MATCH, REC_BRANCH_COND_SCALAR, MUST_LOOP_AT_LEAST_ONCE, MUST_BRANCH_COND_NO_XZ, MUST_ASSIGN_NO_DUP_CONDITION, MUST_OP_NO_ARITH_OVERFLOW, MUST_LOOP_FOR_CONST_BOUNDS, MUST_LITERAL_WIDTH_MATCH, MUST_OP_NO_SIGN_MIX, REC_LITERAL_EXPLICIT_BASE_WIDTH |
| procedural-blocks | 9 | MUST_COMB_BLOCKING_ASSIGN, MUST_RST_NO_GLITCHY_COMB, MUST_CLK_NO_COMB_CLOCK, MUST_LATCH_NO_GATE_PRIMITIVE, MUST_LATCH_NO_COMB_LOOP, REC_COMB_IF_HAS_ELSE, MUST_SEQ_NONBLOCKING_ASSIGN, REC_LATCH_SEPARATE_FROM_COMB, MUST_COMB_NO_FEEDBACK |
| sensitivity-lists | 2 | MUST_SENS_NO_OR_SEPARATOR, MUST_SENS_LIST_COMPLETE_MINIMAL |
| synthesizable-subprograms | 7 | MUST_FSM_HAS_INITIAL_STATE, MUST_FUNC_NO_RECURSION, MUST_TASK_IO_WIDTH_MATCH, MUST_FUNC_RETURN_WIDTH, MUST_INITIAL_FORBIDDEN, MUST_TASK_NO_TIMING_CONTROL, MUST_FUNC_NO_NONBLOCKING |
| widths-connectivity | 5 | REC_ARRAY_INDEX_SIMPLE, MUST_CONN_PORT_WIDTH_MATCH, MUST_ARRAY_INDEX_IN_RANGE, MUST_ASSIGN_NO_DELAY, MUST_ASSIGN_WIDTH_MATCH |

## Full Rule List

| Rule ID | Kind | Severity | Enforcement |
| --- | --- | --- | --- |
| MUST_OP_NO_XZ_ARITH | must | error | automated_error |
| REC_CLK_SINGLE_DOMAIN | recommended | warning | prompt_warning |
| REC_OP_NO_LOGIC_ON_VECTOR | recommended | warning | prompt_warning |
| MUST_RST_NO_SYNC_ASYNC_MIX | must | error | review_error |
| REC_CASE_NO_OVERLAP | recommended | warning | prompt_warning |
| REC_RST_ONE_SIGNAL_PER_ALWAYS | recommended | warning | prompt_warning |
| MUST_OP_NO_XZ_CONDITION | must | error | automated_error |
| MUST_COMB_BLOCKING_ASSIGN | must | error | automated_error |
| MUST_LOOP_NO_NONINDEX_ARITH | must | error | review_error |
| REC_REPEAT_CONST_COUNT | recommended | warning | prompt_warning |
| MUST_RST_NO_GLITCHY_COMB | must | error | review_error |
| MUST_LOOP_NO_RESET_LOGIC_MIX | must | error | review_error |
| MUST_CASE_ITEM_IN_RANGE_WIDTH | must | error | review_error |
| MUST_OP_REL_WIDTH_MATCH | must | error | automated_error |
| MUST_FSM_HAS_INITIAL_STATE | must | error | review_error |
| MUST_SYNTH_NO_RESET_OVERRIDE | must | error | review_error |
| REC_BRANCH_COND_SCALAR | recommended | warning | prompt_warning |
| MUST_CLK_NO_COMB_CLOCK | must | error | automated_error |
| MUST_CASE_CONTROL_NOT_CONSTANT | must | error | review_error |
| REC_ARRAY_INDEX_SIMPLE | recommended | warning | prompt_warning |
| MUST_LOOP_AT_LEAST_ONCE | must | error | review_error |
| MUST_RST_NO_ASYNC_TO_DATA_PIN | must | error | review_error |
| MUST_FSM_DEFAULT_RESET_REGS | must | error | review_error |
| MUST_LATCH_NO_GATE_PRIMITIVE | must | error | review_error |
| REC_CLK_AVOID_GATING | recommended | warning | prompt_warning |
| MUST_CONN_PORT_WIDTH_MATCH | must | error | review_error |
| MUST_FSM_NO_DEAD_UNREACHABLE | must | error | review_error |
| REC_RST_DEDICATED_GENERATOR | recommended | warning | prompt_warning |
| REC_RST_NO_INTERNAL_ASYNC_SRC | recommended | warning | prompt_warning |
| MUST_BRANCH_COND_NO_XZ | must | error | automated_error |
| REC_RST_NO_SET_RESET_PAIR | recommended | warning | prompt_warning |
| MUST_ASSIGN_NO_DUP_CONDITION | must | error | review_error |
| MUST_LATCH_NO_COMB_LOOP | must | error | review_error |
| MUST_CASE_ITEM_CONSTANT_ONLY | must | error | review_error |
| MUST_FUNC_NO_RECURSION | must | error | automated_error |
| MUST_CLK_NO_REGOUT_CLOCK | must | error | automated_error |
| REC_CASE_DEFAULT_NOT_XZ | recommended | warning | automated_warning |
| REC_COMB_IF_HAS_ELSE | recommended | warning | prompt_warning |
| MUST_FF_INIT_ON_RESET | must | error | review_error |
| MUST_CASE_HAS_DEFAULT | must | error | automated_error |
| REC_FSM_LIMIT_STATE_COUNT | recommended | warning | prompt_warning |
| MUST_FF_NO_MIXED_RESET_STYLE | must | error | review_error |
| REC_SYNTH_NO_FULL_CASE_ATTR | recommended | warning | prompt_warning |
| MUST_TASK_IO_WIDTH_MATCH | must | error | review_error |
| MUST_FF_RESET_CONDITION_MATCH | must | error | review_error |
| MUST_SENS_NO_OR_SEPARATOR | must | error | automated_error |
| REC_RST_NO_LOGIC_IN_ASYNC_PATH | recommended | warning | prompt_warning |
| REC_FSM_MIN_TRANSITION_FLIPS | recommended | warning | prompt_warning |
| REC_CLK_SINGLE_EDGE | recommended | warning | prompt_warning |
| MUST_FUNC_RETURN_WIDTH | must | error | review_error |
| MUST_OP_NO_ARITH_OVERFLOW | must | error | review_error |
| MUST_LOOP_FOR_CONST_BOUNDS | must | error | automated_error |
| MUST_INITIAL_FORBIDDEN | must | error | automated_error |
| MUST_LITERAL_WIDTH_MATCH | must | error | review_error |
| MUST_SENS_LIST_COMPLETE_MINIMAL | must | error | review_error |
| MUST_SUBPROGRAM_NO_GLOBAL_WRITE | must | error | review_error |
| REC_CASE_NO_CASEX_CASEZ | recommended | warning | automated_warning |
| MUST_SEQ_NONBLOCKING_ASSIGN | must | error | automated_error |
| MUST_ARRAY_INDEX_IN_RANGE | must | error | review_error |
| MUST_ASSIGN_NO_DELAY | must | error | automated_error |
| MUST_CLK_ONLY_CLOCK_PIN | must | error | review_error |
| MUST_TASK_NO_TIMING_CONTROL | must | error | automated_error |
| MUST_OP_NO_SIGN_MIX | must | error | review_error |
| REC_LATCH_SEPARATE_FROM_COMB | recommended | warning | prompt_warning |
| MUST_COMB_NO_FEEDBACK | must | error | review_error |
| MUST_ASSIGN_WIDTH_MATCH | must | error | automated_error |
| REC_LITERAL_EXPLICIT_BASE_WIDTH | recommended | warning | automated_warning |
| MUST_FUNC_NO_NONBLOCKING | must | error | automated_error |

## Prompt And Review Contract

Generation, planning, and review prompts must carry the complete rule ID list.
Manifest checks must state REC deviations in implementation_assessment or
reviewability_assessment. Static lint issues must include the matching rule ID in
their message so each finding can be traced to this catalog.
