# Project governance protocol

Use this reference whenever clone or benchmark work changes project state.

## Source of truth

`project/plan.json` is authoritative for current status. `PROJECT.md` explains
the process. README files provide entry points but must not maintain a competing
roadmap.

The plan is validated by `websitebench/schemas/project-plan.schema.json` and
`clawbench-project`.

## Planning sequence

1. Read `clawbench-project status`.
2. Match the request to an existing backlog item or milestone.
3. Confirm the owner role and dependencies.
4. Add a new item only when the result is genuinely distinct.
5. Write objective acceptance criteria before implementation.
6. Record architecture consequences as a decision or risk.

Use stable lowercase hyphenated IDs. Dependencies within milestones or backlog
must reference IDs in the same collection.

## Status meaning

- `planned`: accepted work whose implementation has not started.
- `in_progress`: active work with meaningful implementation or verification.
- `blocked`: work cannot advance because of a named external, ownership, or
  dependency condition; `blocked_by` is mandatory.
- `complete`: every declared criterion is met and evidence is recorded.

Difficulty, uncertainty, or an incomplete attempt is not a blocker. A code
change without its required tests, calibration, or review is not complete.

## Evidence rules

Good evidence includes:

- versioned contract, manifest, schema, report, or source paths;
- exact automated test commands and archived results;
- Harbor run IDs and immutable artifacts;
- CTRF, reward, log, screenshot, video, or bundle hashes;
- a dated human review record with the reviewed state matrix.

A filename that does not exist, an intended future test, or an undocumented
manual impression is not evidence.

Completed workstreams, milestones, and backlog items require non-empty evidence.
Passed release gates also require evidence.

## Release gates

Treat gates independently:

1. plan and authoring validity;
2. regression suites and lint;
3. real Harbor NOP/oracle calibration;
4. visibility and network isolation audit;
5. human browser review;
6. release manifest, licensing, and redistribution review.

One gate cannot waive another. Use `clawbench-project check-release`; a return
code of 3 means valid plan but not release-ready.

## Change review

When changing a schema, ID, score, runtime boundary, or authoring layout, inspect
all consumers:

- CLI and Python loaders;
- scaffolds and templates;
- materialized bundles;
- offline-clone harness;
- Viewer discovery/reviews;
- existing site and instance fixtures;
- tests, docs, and this skill;
- project risks, decisions, and release gates.

Preserve unrelated dirty changes. If another branch or person owns overlapping
work, record a blocker and avoid destructive reconciliation.

## Expansion review

A proposed instance adds benchmark value only if it contributes meaningful
coverage in at least one dimension:

- a new core or failure journey;
- a new visual/interaction state;
- a new authorization or identity boundary;
- a new server-side state transition;
- new persistence, reset, idempotency, inventory, or money semantics;
- a new robustness failure mode.

Changing branding or layout around the same trivial CRUD path is not sufficient.

## Required handoff

The final handoff must state current milestone/backlog status, evidence produced,
tests run, blockers, and release readiness. If the plan was not updated, explain
why the request did not change project state.
