# Harbor live-site corpus readiness audit — 2026-07-23

## Scope

This audit covers the portable live-site inventory introduced in commit
`cbe9b047d1d52f7b91f18bc2703fb947289fac13` and the Harbor authoring corpus
present in this checkout. It is a readiness audit, not a release calibration.

Inventory under review:

- 129 discovered tasks;
- 61 normalized platforms;
- 62 first-party origins;
- frozen source: `websitebench/corpora/claw-bench-v2/live-site-inventory.json`.

## Result

**Status: not ready for Harbor calibration or release.**

The Harbor authoring toolchain and its focused tests pass, but the repository
currently contains no authored Harbor site or instance:

| Item | Count |
| --- | ---: |
| Inventory tasks | 129 |
| Inventory platforms | 61 |
| Inventory first-party origins | 62 |
| `harbor/sites/*/site.yaml` | 0 |
| `harbor/instances/*/instance.yaml` | 0 |
| Missing platform adapters | 61 |

`clawbench-harbor validate-corpus` therefore fails closed with:

```text
Harbor authoring validation failed:
- harbor: no sites/*/site.yaml manifests found
```

This is consistent with `project/plan.json`: the first real site is still being
finalized, `author-first-instance` is planned, `calibrate-first-instance` is
planned, and the Harbor calibration, isolation, human-review, and release
manifest gates remain pending.

## Checks performed

The commands were run from the repository root with the local `src` tree on
`PYTHONPATH` where required.

| Check | Result |
| --- | --- |
| `python -m clawbench.harbor.cli validate-corpus --corpus-root harbor` | Failed as expected: no site manifests |
| `python -m pytest tests/harbor -q` | 8 passed |
| `python -m pytest tests/harbor tests/project tests/offline_clone/test_live_site_inventory.py -q` | 15 passed |
| `uvx ruff@0.15.12 check src/clawbench/harbor tests/harbor tests/offline_clone/test_live_site_inventory.py` | Passed |
| `python -m clawbench.project.cli validate` | Passed |
| `python -m clawbench.project.cli check-release` | `not_ready` |

Runtime availability on the audit machine:

| Runtime | Available |
| --- | --- |
| Python 3.12.3 | Yes |
| Docker / Docker Compose | No |
| Harbor CLI/runtime | No |

## Evidence classification

Direct structural evidence:

- the portable inventory parses and its task/platform/origin denominators pass
  repository tests;
- the Harbor schema, scaffold, materialization, scoring, and verifier helper
  tests pass;
- corpus validation rejects the empty authoring corpus instead of reporting a
  false success;
- project governance validation passes and release status remains
  `not_ready`.

Unavailable evidence:

- a real `site.yaml` and `instance.yaml`;
- an instance validation and materialized bundle;
- a `bundle-manifest.json` visibility and hash audit;
- Docker/Compose reference and candidate isolation;
- Harbor NOP, oracle, and repeated-oracle run IDs, CTRF, reward, and artifacts;
- candidate proxy-resistance and judge-network isolation results;
- human browser review of reference versus candidate;
- licensing and redistribution review for a release manifest.

No unavailable item is inferred from unit tests or marked as passed.

## Required next gate

Before another Harbor review:

1. finish the declared release gates for one reference site;
2. author `harbor/sites/<site-id>/site.yaml` and its trusted verifier;
3. author one task-specific `harbor/instances/<instance-id>/instance.yaml`;
4. validate and materialize that exact instance;
5. use a Docker/Compose-capable Harbor runtime to run NOP, oracle, repeated
   oracle, visibility/network isolation, and human browser review;
6. archive hashes, run IDs, CTRF, reward, screenshots, logs, and the reviewed
   human checklist before changing any release gate to passed.

The remaining 60 platforms should not be bulk-marked reviewed from this
readiness audit. They must traverse the same per-site and per-instance gates in
bounded batches.
