# Current research state

Updated: 2026-07-20

## Current active claim

**C001 (provisional):** A registry-resolved, policy-diverse three-variant Commerce matrix with public/hidden seed pairs can distinguish the four `gpt-5.5-codex` reasoning-effort settings without conflating candidate quality with evaluator or infrastructure failure.

## Current paper shape

Exploration / benchmark-calibration study. No paper claim is considered supported until the real Core matrix and Amazon harness calibrations run on an eligible runner.

## Current residual contribution

The repository now contains the offline-verifiable production chain and experiment contract: Registry → SiteDriver → trusted run manifest → VariantCompiler → host facts scoring → typed attempts → SQLite Batch. Three policy-distinct variants compile deterministically and the frozen matrix expands to 12 jobs / 120 Journey-Seed executions.

## Literature coverage and prior work

Not in scope for this engineering milestone. No literature-backed novelty claim is being made.

## Evidence status

- Baseline before implementation: `75 passed, 1 skipped`.
- Offline implementation checks: `111 passed, 1 skipped`; Ruff is clean; all 18 JSON schemas validate; Registry resolution, deterministic compile/check, task-v2 dry run, strict public/private seed scans, facts/attempt behavior, frozen-input refusal, batch planning/lease renewal/recovery, calibration schema, and Viewer separation are implemented.
- Local reference behavior evidence: the independent private Judge executed all five browser-observable journeys on both seeds for Foundry, Ember, and Harbor; all three facts documents validated and each reference scored 10/10 exact Journey-Seed passes (30/30 total, Journey dimension 40/40). The run exercised real mailbox verification/reset links, single-use tokens, one-success/one-409 checkout concurrency, Foundry tier pricing/cutoff, Ember reservation expiry/account-lifetime limit, and Harbor pickup-required/capacity/cutoff behavior. Paired desktop/mobile Reference-vs-Reference captures both scored 1.0 similarity for every variant; ten-way health p95 was below the 1000 ms target for all three. This exercised the application/Judge/browser boundary without Docker isolation.
- Real model evidence: **not run**. This host has a Docker CLI, but the daemon is unreachable, Compose v2 is unavailable, and `OPENAI_API_KEY` is unset; the required container/model path is therefore unavailable, as anticipated by the experiment plan.
- HITL evidence: **not run and not scheduled**; Issue #8 remains a later experiment digest.

## Top risks

1. The candidate/evaluator Docker path has not been exercised on an eligible runner.
2. One repetition measures task discrimination, not stochastic variance.
3. The generic commerce runtime/Judge passed local process-boundary checks, but still needs the Docker network/mount/build isolation smoke before model differences are interpreted.
4. Amazon calibration must succeed at all four efforts, with xhigh completing the mandatory task, or the main matrix remains uninterpretable.

## Next decision

Run E001 and E002 on a host with Docker Engine, Compose v2, `OPENAI_API_KEY`, and `gpt-5.5-codex` access; apply the precommitted Go/No-Go rules without relaxing thresholds after seeing results.

## Next action

Use the frozen batch plan/ledger commands documented in `websitebench/README.md`, publish four schema-valid Amazon calibration records, then update `research_state/06_experiments/experiment_log.md` and this file with observed results.
