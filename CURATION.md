# Curation record

This repository was assembled on 2026-07-19 from
`/mnt/cpfs/ClawBench` into `/mnt/cpfs/ClawBench-WebsiteBench` as a fresh Git
repository. The source checkout was copied, never moved or deleted.

## Included

- complete `websitebench/` protocol, Northstar reference, judge, fixtures,
  services, Dockerfiles, Compose topology, gates, and schemas;
- Web2Code host orchestration, Candidate isolation, scoring, reporting, and
  visual metrics;
- authenticated Viewer, deployment files, tests, and three explicit legacy
  compatibility adapters;
- WebsiteBench and Viewer tests;
- final Amazon Gate 1 source capture, Gate 2 review, Gate 3 fidelity run, Gate 4
  approved BrowserUse run, independently authored clone, and corresponding
  ClawBench development task;
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

- Amazon task and verifier paths now point to `materials/amazon/clone` and
  `tasks/clawbench`.
- The ClawBench task schema is retained at `schemas/task.schema.json`.
- A minimal Python package exposes only `clawbench-web2code` and
  `clawbench-viewer`.
- Viewer container context excludes the large Amazon research material.
- Three small legacy clones/tasks remain solely because they exercise Viewer
  compatibility and score-provenance boundaries.

Machine-generated Amazon evidence reports were copied without modification.
Their hashes are recorded in `materials/amazon/EVIDENCE_SHA256SUMS`.

