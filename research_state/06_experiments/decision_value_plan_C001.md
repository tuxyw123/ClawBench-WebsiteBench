# C001 decision-value experiment plan

Mode: **Exploration**

## Evidence classification

- Necessary: E001 Core clone matrix and E002 Amazon harness calibration.
- Supporting: reference-vs-reference checks, deliberate policy mutation detection, batch crash/lease recovery, and public/private leak scans.
- Cuttable for C001: more than one repetition, more variants, alternative models, visual-only ablations, and checkpointed HITL.
- Future work: repetitions for stochastic variance, broader family coverage, and real Issue #8 Human+Agent sessions.

## E001 — Three-variant Core clone matrix

**Problem addressed:** Can the new production chain generate attributable, hidden-data clone outcomes with enough resolution to compare four reasoning efforts?

**Which part of the stated problem does this experiment answer?** It jointly tests deterministic task production and whether reasoning effort creates a measurable exact Journey-Seed pass-rate gradient across materially different policy state machines.

**Hypothesis:** Across Foundry, Ember, and Harbor, `gpt-5.5-codex` xhigh has a 40%–90% exact pass rate, the best/worst effort gap is at least 15 percentage points, and public/hidden aggregate gap is at most 20 percentage points.

**Decision this experiment informs:** Continue to more variants/repetitions, simplify or expose a policy state machine, harden task boundaries, or pause all model comparison to repair evaluator/infrastructure attribution.

**Minimum valid setup:**

- 3 registered variants, all inheriting the Commerce validation split;
- 4 efforts (`xhigh`, `high`, `medium`, `low`), Core, 1 repetition, concurrency 2;
- 5 fixed journeys over one public and one hidden seed per variant;
- 12 terminal attributable jobs and 120 planned Journey-Seed executions;
- 2h / 400k tokens / 400 browser actions / 10 builds per job;
- frozen registry snapshot, prompt, commit/tree, Compose inputs, budgets, and public inputs;
- candidate retries 0, transient evaluator retries ≤1, allowlisted infrastructure retries ≤2 with 5s/30s backoff.

**Invalid cheap proxy to avoid:**

- static page or API-only completion in place of a persistent browser-observable clone;
- public-seed-only scoring;
- synthetic zero scores for missing facts;
- replacing reservations, tier pricing, store inventory, or shared slot capacity with a generic cart cap;
- model output inspection without actually building and evaluating the candidate;
- automated hints represented as Human+Agent evidence.

**Expected outcomes and decisions:**

1. All gates pass → expand variants and repetitions, then execute Issue #8 with a new digest.
2. xhigh <40% or any variant all-zero → reduce that policy state machine or improve public observability, then mint a new plan digest.
3. xhigh >90% and low/medium near-perfect → increase business boundary difficulty.
4. Public/hidden gap >20pp → audit fixture generalization, public leakage, and candidate hard-coding.
5. Effort spread <15pp → C001 lacks discrimination; do not claim reasoning-effort separation.
6. Evaluator/infra failures are material → pause model comparison and repair the production chain.

**Stop condition:** Stop after all 12 jobs reach attributable terminal outcomes within retry limits and the precommitted metrics can be computed. Do not add low-information configurations during C001.

## E002 — Amazon-136 harness calibration

**Problem addressed:** Could legacy browser-harness failure, rather than clone ability, explain the Core matrix?

**Which part of the stated problem does this experiment answer?** It checks that each effort can produce a valid 20-minute browser-task record and that xhigh can complete the mandatory retained Amazon task.

**Hypothesis:** All four efforts yield schema-valid calibration records, and xhigh passes the mandatory task without a harness error.

**Decision this experiment informs:** Interpret E001, or suspend interpretation and diagnose the browser harness.

**Minimum valid setup:** One Core calibration per effort under the original 1200-second limit, recording PASS/FAIL, step pass rate, tokens, browser actions, elapsed time, and harness error.

**Invalid cheap proxy to avoid:** Serving the static clone, replaying a stored trajectory, counting DOM checks without running the browser task, mapping calibration to `/100`, or adding it to the official leaderboard.

**Expected outcomes and decisions:** Four valid records plus xhigh mandatory PASS permits E001 interpretation; any missing/invalid record or xhigh mandatory failure triggers harness diagnosis first.

**Stop condition:** Stop after one valid record for each effort. Additional repetitions belong to a later calibration study.
