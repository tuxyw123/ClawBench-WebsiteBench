# ClawBench Web2Code2Web

This directory contains the corpus and protocol for the Web2Code2Web benchmark.
It is deliberately separate from the existing live-web task suites under
`test-cases/`.

The first corpus item is **Northstar Market**, a synthetic, white-label
commerce application. It is inspired by common commerce interaction patterns,
but it does not use Amazon source code, data, trademarks, or media.

## Trust boundary

- `northstar-market/public/` is visible to candidate-building agents.
- `northstar-market/reference/` will contain the private reference
  implementation and is never mounted into an agent or browser-gateway
  container.
- `northstar-market/judge/` will contain private evaluators and hidden fixtures.
- `schemas/` is public and defines the cross-site benchmark protocol.

An agent may explore a running reference only through the controlled browser
gateway. The agent may edit and build its candidate workspace, but it may not
read the reference filesystem, browser profile, response bodies, bundles,
source maps, cache, or hidden evaluator files.

## Human-in-the-loop gates

The Northstar pilot is delivered in four review gates:

1. **W1 Protocol freeze** -- PRD, manifest, fixture/reset/clock contract,
   candidate output contract, checkpoints, scoring, and report schema.
2. **W2 Reference behavior** -- reference app, mailbox, deterministic data,
   state machine, and business tests.
3. **W3 Isolation and builder** -- container networks, controlled BrowserUse
   gateway, Codex runner, build service, logging, and anti-cheat checks.
4. **W4 Evaluation and release** -- multi-seed hidden tests, concurrency,
   visual and functional scoring, failure reports, and end-to-end pilot runs.

Work after each gate normally requires explicit human approval. The Northstar
pilot's W1 gate was explicitly approved, and the user then authorized automatic
progress through W2--W4. Approved files are versioned: behavior changes require
a manifest version bump and a fresh gate.

## Registry-resolved production chain

`registry.yaml` is the sole mapping for site drivers and family splits. A
`websitebench.driver.v1` resolves to an immutable `ResolvedSite`; run
preparation writes a host-only, digest-addressed
`websitebench.run-manifest.v1`. The snapshot lives below the run's `trusted/`
directory and is never mounted into the Agent or browser role.

Validate or prepare any registered site without Docker:

```bash
PYTHONPATH=src python -m clawbench.web2code.run validate \
  --site foundry-wholesale

PYTHONPATH=src python -m clawbench.web2code.run pilot \
  --site foundry-wholesale --track core \
  --model gpt-5.5-codex --thinking-level xhigh --dry-run
```

The dry-run export contains task v2, public contracts, public fixtures, and
public schemas. Private reference code, hidden fixtures, Judge assertions,
driver data, runtime secrets, and the trusted snapshot remain host inputs.
Public site manifests enumerate public seeds only. The private Variant spec is
the Registry-owned source for public, hidden, and concurrency execution seeds;
those values are frozen inside the host-only run manifest and never copied to
the Agent or builder preview.
Task v1 and result v1 readers remain valid. Existing site-v1 manifests are
accepted only when `family_id` and `split` match the Registry.

## Deterministic Commerce variants

The `white-label-commerce-v1` DSL is strict data. Policies and assertions select
allowlisted kinds; split overrides, scripts, executable templates, dynamic
imports, and unknown fields fail validation.

```bash
PYTHONPATH=src python -m clawbench.web2code.variants compile --all
PYTHONPATH=src python -m clawbench.web2code.variants compile --all --check
```

Compilation discovers specs through the Registry and emits public contracts,
fixtures, smoke cases, private fixtures/assertions, task v2, and a digest lock
in canonical order. `--check` is read-only and reports drift. Northstar is the
standard golden; Foundry, Ember, and Harbor supply case-tier, reservation, and
store/slot state machines. The shared reference runtime persists accounts,
sessions, tokens, guest/account carts, reservations, inventory, pickup slots,
idempotency records, orders, and controlled time. Its private Judge executes
five real behavior journeys on both public and hidden fixtures and emits facts
only; the host remains the sole scorer/report writer.

## Resumable batches

Batch plans freeze Registry/run-manifest inputs, the Agent prompt, code commit
and tree, Compose and container build/image inputs, budgets, and public inputs. The SQLite ledger is the
source of truth for jobs, Journey-Seed executions, leases, attempts, retry
deadlines, outcomes, and artifacts.

```bash
PYTHONPATH=src python -m clawbench.web2code.batch plan \
  --ledger artifacts/websitebench/c001/batch.sqlite3 \
  --out artifacts/websitebench/c001/plan.json \
  --site foundry-wholesale --site ember-drop --site harbor-pickup \
  --model gpt-5.5-codex \
  --thinking-level xhigh --thinking-level high \
  --thinking-level medium --thinking-level low \
  --track core --repetitions 1 --concurrency 2

PYTHONPATH=src python -m clawbench.web2code.batch run \
  --ledger artifacts/websitebench/c001/batch.sqlite3 \
  --plan artifacts/websitebench/c001/plan.json

PYTHONPATH=src python -m clawbench.web2code.batch resume \
  --ledger artifacts/websitebench/c001/batch.sqlite3 \
  --plan artifacts/websitebench/c001/plan.json
```

Back up the SQLite file together with `-wal` and `-shm` while writers are
active, or stop workers/checkpoint WAL before copying. An expired running lease
is closed as an auditable infrastructure interruption before retry. Candidate
failures never retry; transient evaluator failures retry once; allowlisted
infrastructure failures retry twice at 5 and 30 seconds. Scheduler state is
separate from attempt attribution. Active workers renew their leases throughout
long Agent/evaluator runs, so another scheduler cannot reclaim a healthy job.
Exit 0 means all jobs are terminal; exit 2
means work remains; single-run invalid/missing evaluator facts use exit 3.

The checked-in experiment definition is
`experiments/clone-quality-matrix.yaml`. It creates Core jobs only. Issue #8
checkpointed HITL requires a later experiment digest and real human messages.

## Amazon harness calibration

Amazon-136 is retained development/calibration material, not a Registry site.
It does not become a white-label Registry variant: the Amazon renderer and
exact task request contract remain site-specific. Its SQLite
`AmazonCommerceAdapter` now implements the same `AccountOrderCommerce`
Interface as the JSON-backed compiled runtime, so account lifecycle, guest-cart
migration, checkout, order ownership, and cancellation can be exercised on the
Amazon surface without replacing its benchmark identity. Canonical paths and
addresses are declared once in `materials/amazon/runtime-manifest.json`.
`websitebench.calibration-result.v1` records PASS/FAIL, step rate, tokens,
browser actions, elapsed time, and harness errors under the original 20-minute
limit. The Viewer labels these records `Calibration — Unranked`; they never use
`/100`, enter the Commerce result contract, or affect the leaderboard.
