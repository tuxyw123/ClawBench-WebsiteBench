---
name: build-offline-site-clone
description: Build, rebuild, audit, govern, or package a high-fidelity offline website clone in ClawBench WebsiteBench. Use when working on frozen source evidence, local resource closure, browser-visible frontend states, required backend semantics, deterministic reset, Browser Use exploration, Playwright/HTTP verification, Harbor site/instance authoring, benchmark expansion, calibration, or release readiness.
---

# Build and Govern an Offline Site Clone

Treat a website clone as both a purpose-driven reconstruction and a managed
benchmark asset. A visually plausible page is not complete until its frozen
journeys, backend invariants, isolation, verification, calibration, and release
evidence are handled.

## Establish repository state first

Locate the repository root containing `pyproject.toml`, `PROJECT.md`, and
`project/plan.json`.

Read these sources before changing a clone or benchmark:

1. `PROJECT.md`
2. `project/plan.json`
3. `docs/offline-clone-harness.md`
4. `docs/harbor-fullstack-benchmark.md` when Harbor packaging or scoring is in scope
5. The target site's README, scope contract, evidence manifest, and known-gap report

Run:

```bash
clawbench-project validate
clawbench-project status
```

Use the status output to identify the owning role, current milestone, blockers,
dependencies, release gates, and the highest-priority unfinished work. Do not
silently broaden the task to unrelated blocked work.

Read [project-governance.md](references/project-governance.md) when adding,
closing, blocking, or releasing project work.

## Select the active track

Choose only the tracks needed by the request:

- **Clone track:** capture, build, audit, or improve the offline reference site.
- **Harbor track:** create or change a reusable `site` contract, task-specific
  `instance`, trusted verifier, bundle, or calibration.
- **Governance track:** update status, backlog, risk, decision, compatibility,
  release evidence, or benchmark expansion rules.

A real release instance normally traverses all three tracks, but an isolated
frontend fix does not automatically authorize changing scoring or project scope.

## Expand from a live-site inventory

Read [corpus-expansion.md](references/corpus-expansion.md) when the request
starts from a task/site inventory or asks for multiple online sites.

For the bundled claw-bench-v2 corpus, use
`websitebench/corpora/claw-bench-v2/live-site-inventory.json` as the portable
task/platform fact source. Do not require a sibling claw-bench-v2 checkout.

Normalize the inventory before implementation:

- group tasks by product/platform contract, not blindly by task or hostname;
- preserve every first-party source origin for a grouped platform;
- keep documented/discovered count mismatches as blockers or warnings instead
  of inventing missing tasks;
- create one reusable site adapter per normalized platform and task-specific
  Harbor instances only after the site contract is stable;
- treat an older replica's `verified` label as a claim to re-audit under the
  current harness, not as transferable release evidence.

Scale work in bounded platform batches. A batch plan does not relax per-site
source, asset, frontend, backend, release, Harbor calibration, or human-review
gates.

## Register work before implementation

Ensure the requested outcome exists in `project/plan.json` as a workstream,
milestone, or backlog item with:

- one role owner;
- a priority;
- objective acceptance or exit criteria;
- explicit dependencies;
- current status;
- evidence paths when already available.

If the work is already represented, update it instead of creating a duplicate.
Keep IDs stable after publication. Add a risk or decision when a change affects
isolation, scoring, identifiers, compatibility, source rights, or the benchmark
definition.

## Freeze the clone contract

Before implementation, freeze:

- site purpose and user roles;
- core and failure journeys;
- route/state/viewport matrix;
- semantic backend invariants;
- explicit non-goals;
- direct, unavailable, inferred, and structural-only evidence states.

Use anonymous, read-only capture by default. Redact credentials, tokens,
cookies, personal data, authorization headers, and browser profiles. Preserve
source ownership and redistribution limits.

Do not infer a state merely because it is convenient to implement. Mark
unobserved states honestly.

## Close resources before polishing

Inventory every required image, font, icon, stylesheet, script, and response.
Store allowed assets locally and map each source URL to a deterministic local
path. Reject accidental remote runtime dependencies, telemetry, production API
calls, payment, fulfillment, and email delivery.

Use the repository's offline-clone harness:

```bash
clawbench-offline-clone validate --site <clone-root>
clawbench-offline-clone status --site <clone-root>
```

Follow the exact subcommands documented by
`docs/offline-clone-harness.md`; do not invent completion evidence from a single
static check.

## Build the frontend state matrix

Implement route shells and shared chrome first, then expand each frozen state:

- default and alternate viewport states;
- loading, empty, validation, success, error, and unauthorized states;
- overlays, dialogs, menus, drawers, sticky elements, and focus behavior;
- navigation, form submission, search/filter/sort, and state transitions.

Compare source and clone at matched route, viewport, scroll position, data, and
interaction state. Fix structural geometry before cosmetic detail.

## Implement only required backend semantics

Derive API and persistence behavior from frozen journeys and invariants. Enforce
identity, authorization, validation, state transitions, money/inventory rules,
idempotency, and deterministic reset on the server rather than trusting the UI.

Keep browser-visible and admin/reset control planes separate. Never expose a
hidden verifier expectation through a public endpoint.

## Verify iteratively

Use four distinct evidence classes:

- structural checks for files, manifests, routes, and offline closure;
- direct HTTP checks for API contracts and backend invariants;
- browser checks for real UI behavior and visual states;
- human review for experience, misleading claims, and known gaps.

Machine browser checks do not replace human browser review. Human review does
not replace deterministic formal tests.

## Package the Harbor benchmark

Read [harbor-benchmark.md](references/harbor-benchmark.md) before authoring or
materializing an instance.

Keep reusable site sources under `harbor/sites/<site-id>/` and task overlays
under `harbor/instances/<instance-id>/`.

```bash
clawbench-harbor init-site ...
clawbench-harbor init-instance ...
clawbench-harbor validate --instance harbor/instances/<instance-id>
clawbench-harbor validate-corpus --corpus-root harbor
clawbench-harbor materialize \
  --instance harbor/instances/<instance-id> \
  --out harbor-dist/<instance-id>
```

The Agent explores the browser-only reference with Browser Use CLI. The trusted,
separate verifier uses Playwright and direct HTTP checks. Reference source,
hidden fixtures, verifier code, and oracle content must remain unavailable to
the Agent.

Use reference-first sequential differential checks. Do not leave reference
access available in a way that lets the candidate proxy it.

## Calibrate before release

For every real instance:

1. Run the no-op candidate and confirm its score is at or below `nop_max_score`.
2. Run the oracle and confirm its score is at or above `oracle_min_score`.
3. Repeat the oracle and compare node results and exact total.
4. Audit bundle visibility and candidate/reference network separation.
5. Perform the declared human browser review.
6. Record artifacts, CTRF, reward, logs, screenshots, and hashes as evidence.

Static schema validation, template unit tests, or successful materialization are
not substitutes for real Harbor runs.

## Update project truth

After verification, update `project/plan.json`:

- move work to `complete` only when every criterion is satisfied;
- attach concrete evidence to completed work and passed gates;
- set `blocked` only with a specific external or ownership blocker;
- update risks when a trigger changes;
- add or supersede decisions when architecture changes;
- leave release gates pending when their evidence was not produced.

Then run:

```bash
clawbench-project validate
clawbench-project status
clawbench-project check-release
ruff check src tests websitebench
python -m pytest tests/project tests/offline_clone tests/harbor -q
```

Run the relevant site and Viewer suites too. Do not delete tests, weaken
thresholds, or overwrite unrelated dirty work to manufacture a green status.

## Finish with an evidence-backed report

Report:

- the frozen scope and tracks completed;
- files and contracts changed;
- commands and tests actually run;
- direct, structural-only, unavailable, and inferred evidence;
- remaining blockers and known gaps;
- current release-gate result.

Never describe a clone, instance, milestone, or corpus as complete when its
calibration or human-review gate remains pending.
