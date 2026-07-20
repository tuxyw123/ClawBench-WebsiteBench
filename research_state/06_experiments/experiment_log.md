# Experiment log

## 2026-07-20 — C001 implementation checkpoint

### E001 Core matrix

- Status: `ready-for-eligible-runner`; model executions not started.
- Offline plan expansion: 12 jobs, 120 Journey-Seed executions.
- Verification suite: `111 passed, 1 skipped`; Ruff clean; 18 JSON schemas valid; Registry/topology validation clean.
- Deterministic compiler: clean `compile --all` followed by `compile --all --check` succeeds.
- Registry: Northstar, Foundry, Ember, and Harbor resolve to `white-label-commerce-v1 / validation` through typed drivers. Public manifests expose public seeds only; hidden/concurrency seeds and private Variant mounts remain in the digest-addressed trusted snapshot.
- Reference policy checks: Foundry tiers/case/cancel, Ember reservation transfer/TTL/decline/final-sale, and Harbor inventory+capacity/cancel restoration covered by pure and persistent-state tests.
- Reference-vs-reference behavior calibration: the independent facts-only Judge ran the five journeys on each public/hidden pair. Foundry 10/10, Ember 10/10, and Harbor 10/10 exact Journey-Seed passes; all facts documents passed `websitebench.facts.v1`, each Journey dimension normalized to 40/40, and paired desktop/mobile screenshots had 1.0 similarity. Real mailbox links and single-use token flows passed; each concurrency probe produced one 200 and one 409; tier pricing, reservation expiry/account-lifetime limits, pickup-required/capacity, and cancellation cutoffs passed on both seeds. Ten-way health p95 remained below 1000 ms for all variants. This was a local application/Judge/browser process-boundary run, not the pending Docker isolation smoke.
- Mutation calibration: tier discount, reservation TTL, and pickup slot capacity mutations fail the independent Judge policy checkpoint for their affected journey.
- Attribution/ledger checks: terminal immutability, retry advice, concurrency cap, active lease renewal, lease expiry, retry wait, and history preservation covered by tests.
- Result: no model-quality observation yet; no Go/No-Go threshold evaluated.

### E002 Amazon calibration

- Status: `not-started`.
- Calibration schema and separate Viewer section implemented.
- No calibration is reported as an official WebsiteBench result or score.

### Blocking environment

The current host has a Docker CLI, but its daemon is unreachable, `docker compose` is unavailable, and `OPENAI_API_KEY` is unset, so it does not provide the required container/model execution path. This is an anticipated execution prerequisite, not a model or candidate outcome. Formal runs must occur on a runner with Docker Engine, Compose v2, `OPENAI_API_KEY`, and `gpt-5.5-codex` access.

### Next log update

Append immutable plan digest, runner image/commit, per-job attribution, exact public/hidden rates, effort spread, per-variant extremes, and four calibration outcomes after execution. Do not overwrite this checkpoint.
