# Domain docs

This repository uses a single-context domain-documentation layout. These rules
tell engineering skills how to consume its domain language and architectural
decisions.

## Before exploring

- Read `CONTEXT.md` at the repository root when it exists.
- If a future `CONTEXT-MAP.md` exists, follow it to each context relevant to the
  requested work instead of assuming a single context.
- Read ADRs under `docs/adr/` that touch the area being changed. If context-level
  ADR directories are introduced later, read the relevant ones too.
- For WebsiteBench protocol work, also read `WebsiteBench.md`,
  `websitebench/README.md`, and the README for the corpus site being changed.

If `CONTEXT.md`, `CONTEXT-MAP.md`, or `docs/adr/` does not exist, proceed
silently. Documentation-producing workflows can create them when domain terms
or architectural decisions are actually resolved.

## Use the domain vocabulary

Use terms as they are defined in `CONTEXT.md` in issue titles, proposals, test
names, and implementation notes. Do not substitute a synonym that the glossary
explicitly rejects. If a required concept is absent, first check whether the
repository already uses another established term; otherwise record the gap for
a documentation workflow.

## Surface ADR conflicts

If proposed work contradicts an existing ADR, call out the conflict explicitly
instead of silently overriding the decision.
