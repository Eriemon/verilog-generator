# Skill Development Standards

This reference captures the active skill-authoring standards used by this repository.

## Frontmatter Rules

- Keep exactly two frontmatter fields in `SKILL.md`: `name` and `description`.
- Keep frontmatter under 1024 characters total.
- Keep `name` lowercase with letters, numbers, and hyphens only.
- Start `description` with `Use when`.
- Keep `description` focused on trigger conditions, symptoms, and contexts.
- Do not summarize the internal workflow in `description`.
- Keep `description` at or below 500 characters.

## Progressive Disclosure Rules

- Keep the main `SKILL.md` focused on trigger conditions, workflow boundaries, and resource navigation.
- Move heavy reference material into `references/`.
- Move reusable scripts, templates, and fixtures into their own folders instead of duplicating large inline content.
- Every `Load ... when ...` resource path named in `SKILL.md` must exist inside the skill directory.

## Pattern Rules

This skill should explicitly preserve the following design-pattern mix:

- Tool Wrapper
- Generator
- Reviewer
- Inversion
- Pipeline

## Evaluation Rules

- Verification claims must be backed by fresh command output.
- Skill-level checks must cover metadata, resource integrity, workflow behavior, and confidence gates.
- Generated Verilog validation must include a hard per-line explanatory comment gate for every non-empty RTL and testbench `.v` code line.
- Effectiveness evidence should compare behavior with and without the skill when a realistic evaluation harness exists.
- Pass-rate delta and failure-mode coverage are preferred over anecdotal success claims.

## Packaging Rules

- Markdown inside the installable skill must remain ASCII-only.
- Installable content must not keep hard dependencies on temporary external reference directories.
- Public skill interfaces must stay stable when internal runtime details evolve.
