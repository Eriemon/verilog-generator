# Script Guide

Detailed commands are registered as structured JSON under `config/registry/`. JSON is the only editable registry source. The generated `config/registry/registry.sqlite3` is a local SQLite FTS5 trigram index for Chinese and English retrieval; never edit it manually or use it to overwrite JSON.

## Ask For Usage

Use the canonical `registry.ask` invocation shown once in `SKILL.md`. Its required argument is the natural-language question; optional fields select `kind`, command `category`, result `limit`, and JSON output. Kinds are `command`, `workflow`, `document`, and `knowledge`. Command categories are `registry`, `validation`, `quality`, `workflow`, `toolchain`, `remote`, `generation`, and `external`. The query returns registered syntax, examples, prerequisites, outputs, risk, and boundaries but never executes a result.

Exit codes are:

- `0`: one or more results.
- `1`: no match.
- `2`: invalid request.
- `3`: missing, corrupt, stale, schema-incompatible, or FTS-incompatible database.

## Build The Index

After changing any command, workflow, document, knowledge, migration, or governance JSON, check or rebuild the generated index:

```text
python -m scripts.python.registry.build_registry .
python -m scripts.python.registry.build_registry . --write
```

The default mode verifies schema, relationships, counts, source digest, and current SQLite state. `--write` atomically rebuilds the database. A missing or stale database is a blocker; the query does not silently scan JSON as a fallback.

## Document Governance

Document registration is already enabled for this skill. The lifecycle is self-contained and scans only `SKILL.md` plus every Markdown file below `references/`, recursively:

```text
python -m scripts.python.registry.manage_document_registry status . --json
python -m scripts.python.registry.manage_document_registry scan . --json
python -m scripts.python.registry.manage_document_registry init . --enable --write --json
python -m scripts.python.registry.manage_document_registry check . --json
python -m scripts.python.registry.manage_document_registry finalize . --write [--confirm-user] --json
```

`status`, `scan`, and `check` are read-only. `init` requires explicit registration authorization. `finalize` writes only after Agent review of document responsibilities, knowledge pointers, duplicate decisions, interface mappings, and current hashes. `--confirm-user` is required only when an adjudication is uncertain and has been returned to the user. Markdown remains authoritative throughout.

Exact or fuzzy similarity is a review signal, not permission to delete text. Consolidate only equivalent statements into a named authoritative document. Keep structural repetition such as table-of-contents headings and rulebook separators when it serves document navigation or parsing. Preserve unique inputs, outputs, stop conditions, risk boundaries, and evidence semantics.

## Command Families

- `registry`: JSON/SQLite build, query, and document governance.
- `validation`: package validation and generated-deliverable closure.
- `quality`: unified VG quality, comment-only equivalence, and lint views.
- `workflow`: route, scaffold, prompt, generate, batch, review, existing-RTL verify, and evaluation flows.
- `toolchain`: dependency checks, explicit installation/skip/adaptation, FPGA routing, and external preflight.
- `remote`: discovery, user-confirmed selection, remote validation, retained-run reporting, and cleanup boundaries.
- `generation`: testbench scaffolding and reproducible corpus analysis.
- `external`: xsim, VCS, Verdi, iverilog/vvp, yosys, and Verilator invocation contracts referenced by this skill.

All public commands used by `SKILL.md` or references must have a command record with entrypoint, subcommand, parameters, invocation templates, examples, prerequisites, outputs, risks, boundaries, and related IDs. Workflows remain separate records that order command IDs without weakening command-level semantics.

## Compatibility And Ownership

Canonical runtime modules live inside this skill under `scripts/python/`; the registry must not depend on an external skill installation. Source development may update JSON and rebuild SQLite. Installed copies are read-only unless the user explicitly authorizes installation or replacement. Query results are guidance, not proof that any command or external tool ran.
