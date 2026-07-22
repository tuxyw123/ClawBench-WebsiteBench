# Curation record

This repository was assembled on 2026-07-19 from
`/mnt/cpfs/ClawBench` into `/mnt/cpfs/ClawBench-WebsiteBench` as a fresh Git
repository. The source checkout was copied, never moved or deleted.

## Included

- reusable offline-clone contracts, harness, validation schemas, and tests;
- authenticated Viewer, deployment files, tests, and three explicit legacy
  compatibility adapters;
- final Amazon Gate 1 source capture and corresponding ClawBench development
  task. The curated checkout initially also contained a local clone and Gate
  2–4 output; those artifacts were removed by the 2026-07-20 reset described
  below;
- project license, notice, benchmark requirements, authoring instructions, and
  standalone packaging metadata.

## Excluded

- original Git histories and remotes;
- repository `.env`, model configuration, credentials, browser profiles,
  generated run output, reviews, SQLite state, WAL files, and caches;
- unrelated ClawBench V1/V2/Lite tasks, model runners, harness images, and
  evaluation output;
- unrelated `claw-bench-v2` training, paper, experiment, and clone corpora;
- duplicate Amazon Gate 1 revisions, Gate 3 revisions 1–2, Gate 4 revisions
  1–7, and temporary verifier output.

## Standalone adaptations

- Amazon source evidence and its development task live under
  `materials/amazon/source-capture` and `tasks/clawbench`.
- The ClawBench task schema is retained at `schemas/task.schema.json`.
- The Python package exposes `clawbench-offline-clone` and `clawbench-viewer`.
- Viewer container context excludes the large Amazon research material.
- Three small legacy clones/tasks remain solely because they exercise Viewer
  compatibility and score-provenance boundaries.

The retained Amazon source reports were copied without modification. Their
hashes are recorded in `materials/amazon/EVIDENCE_SHA256SUMS`.

## Post-curation Amazon reset

On 2026-07-20 the authored Amazon runtime, generated catalog/assets, and all
Gate 2–4 clone-derived verification output were removed because the accepted
checks materially overstated visual fidelity. Gate 1 source evidence, the task
contract, source-capture utilities, and source-capture tests were retained.
The compressed rationale and next-build guardrails are in
`materials/amazon/REBUILD_CONTEXT.md`.

## Retired corpus removal

The former synthetic commerce corpus, its Web2Code orchestration package,
container service topology, fixtures, evaluator, tests, and generated static
publication were removed in July 2026. Cross-site schemas, the Viewer, and the
new offline-clone harness remain independent of that retired corpus.

