# Script Guide

Detailed commands are registered under `config/registry/commands/`, while workflow composition lives in `config/registry/workflows/catalog.json`. Document responsibilities and search metadata have one editable owner in `config/registry/documents/catalog.json`; current duplicate and interface decisions live in `config/registry/governance/reviews.json`. The generated `config/registry/registry.sqlite3` is a local SQLite FTS5 trigram index for Chinese and English retrieval at the agents-md-generator-compliant path; never edit it manually or use it to overwrite JSON.

## Ask For Usage

Use the canonical `registry.ask` invocation shown once in `SKILL.md`. Its required argument is the natural-language question; optional fields select `kind`, command `category`, result `limit`, and JSON output. Kinds are `command`, `workflow`, `document`, and `knowledge`. Command categories are `registry`, `validation`, `quality`, `workflow`, `toolchain`, `remote`, `generation`, and `external`. The query returns registered syntax, examples, prerequisites, outputs, risk, and boundaries but never executes a result.

Exit codes are:

- `0`: one or more results.
- `1`: no match.
- `2`: invalid request.
- `3`: missing, corrupt, stale, schema-incompatible, or FTS-incompatible database.

## Build The Index

After changing any command, workflow, document catalog, or governance review JSON, check or rebuild the generated index:

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

`status`, `scan`, and `check` are read-only. `init` requires explicit registration authorization. `finalize` writes only after Agent review of document responsibilities, search metadata, duplicate decisions, interface mappings, and current hashes. Knowledge query rows are derived from the document catalog rather than maintained as a second JSON source. `--confirm-user` is required only when an adjudication is uncertain and has been returned to the user. Markdown remains authoritative throughout.

Exact or fuzzy similarity is a review signal, not permission to delete text. Consolidate only equivalent statements into a named authoritative document. Keep structural repetition such as table-of-contents headings and rulebook separators when it serves document navigation or parsing. Preserve unique inputs, outputs, stop conditions, risk boundaries, and evidence semantics.

## Command Families

- `registry`: JSON/SQLite build, query, and document governance.
- `validation`: package validation and generated-deliverable closure.
- `quality`: unified VG quality, comment-only equivalence, lint views, and governed external-interface stub generation.
- `workflow`: route, scaffold, prompt, generate, batch, review, existing-RTL verify, and evaluation flows.
- `toolchain`: dependency checks, explicit installation/skip/adaptation, FPGA routing, and external preflight.
- `remote`: discovery, user-confirmed selection, remote validation, retained-run reporting, and cleanup boundaries.
- `generation`: testbench scaffolding and reproducible corpus analysis.
- `external`: xsim, VCS, Verdi, iverilog/vvp, yosys, and Verilator invocation contracts referenced by this skill.

All public commands used by `SKILL.md` or references must have a command record with entrypoint, subcommand, parameters, invocation templates, examples, prerequisites, outputs, risks, boundaries, and related IDs. Workflows remain separate records that order command IDs without weakening command-level semantics.

## External Interface Stubs For VG097

`VG097` reports `inconclusive` when an instantiated module has no statically visible interface. Do not treat that state as a confirmed width mismatch and do not suppress the rule. Generate a pure interface bundle, then pass the resulting `.v` file through the quality or generated-deliverable command's repeatable `--external-interface-source` option.

For XPM, UNISIM primitives, or other available vendor HDL, extract only an explicit module whitelist:

```text
python -m scripts.python.quality.external_interface_stubs extract --source <vendor-file-or-dir> --module <module-name> [--module <module-name> ...] --output <stubs.v>
```

For project-generated IP such as VIO, ILA, Clocking Wizard, Processor System Reset, Block Memory Generator, FIFO Generator, AXI interfaces, AXI-Stream interfaces, or GT PHY wrappers, render a reviewed JSON manifest:

```text
python -m scripts.python.quality.external_interface_stubs render-manifest --manifest <external-ip-interfaces.json> --output <project-ip-stubs.v>
```

The bundled `assets/xilinx_primitive_semantics.json` records the governed exact-name inventory: 49 UNISIM profiles, 19 XPM profiles, and ten project-IP manifest categories, with the full support-level and VG097/VG132/VG146/VG147 boundary contract in `references/rules/xilinx-primitive-exemptions.md`. `assets/project_ip_interface_manifest.template.json` provides ten deliberately renamed `*_example` manifest scaffolds; copy only the needed objects, rename them to exact instantiated module names, and verify every interface field against generated project evidence. The catalog is not an embedded copy of AMD-Xilinx source. Missing definitions, duplicate definitions, unsupported conditional alternatives, invalid manifest fields, profile conflicts, and empty interfaces in P1/P2 or project manifests fail closed; an intentionally empty P3 opaque profile remains recognized but inconclusive. A target RTL definition overrides a same-name external stub; competing external definitions remain an error. Stubs and primitive profiles provide static interface facts only and never replace vendor simulation libraries, elaboration, synthesis, implementation, or hardware evidence.

## Compatibility And Ownership

Canonical runtime modules live inside this skill under `scripts/python/`; the registry must not depend on an external skill installation. Source development may update JSON and rebuild SQLite. Installed copies are read-only unless the user explicitly authorizes installation or replacement. Query results are guidance, not proof that any command or external tool ran.
