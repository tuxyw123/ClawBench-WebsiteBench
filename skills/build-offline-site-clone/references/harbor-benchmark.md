# Harbor full-stack benchmark protocol

Use this reference when turning an offline clone into a scored Harbor task.

## Authoring layers

The stable `site` owns:

- reference runtime and health/reset controls;
- public reference evidence allowed for the Agent;
- verifier implementation and hidden fixtures;
- runtime ports/environment names;
- 100-point dimension weights;
- oracle-supporting reference assets.

The task-specific `instance` owns:

- Agent instruction;
- public starter files and run script;
- contract/API/UI/visual/journey/robustness node selection;
- task-specific verifier overlay and fixtures;
- oracle solution;
- NOP and oracle thresholds.

Do not copy the entire reference site into every instance.

## Runtime roles

| Role | Browser/tool | Visibility |
| --- | --- | --- |
| Agent | Browser Use CLI | instruction, starter, allowed public evidence, browser-only reference |
| Candidate | implementation chosen by Agent | its repository and declared runtime only |
| Reference | Compose sidecar | frozen reference runtime; source hidden from Agent |
| Verifier | Playwright + direct HTTP | reference/candidate public and admin URLs, hidden tests/fixtures |
| Oracle | solution runner | available only during calibration |

The Agent's exploratory browser trace is not a scoring oracle. Playwright tests
must run in the separate trusted verifier.

## Differential order

1. Reset reference and candidate independently.
2. Seed equivalent public state.
3. Probe reference and persist normalized expectations.
4. Remove or isolate reference access from candidate influence.
5. Probe candidate with the same inputs.
6. Compare normalized contract, API, DOM/state, visual, journey, and robustness
   evidence.
7. Emit per-node results, CTRF, artifacts, logs, and reward.

Normalize nondeterministic IDs, timestamps, ports, origins, and generated tokens
only when the contract explicitly allows it. Never normalize away a real
semantic difference.

## Required node families

- `contract`: startup, health, reset, offline closure, expected routes/files.
- `api`: validation, authorization, state transitions, persistence, errors.
- `ui`: route/state behavior and user interaction.
- `visual`: matched viewport and state comparison with declared tolerances.
- `journey`: end-to-end cross-page and backend workflow.
- `robustness`: invalid input, refresh/restart, isolation, proxy resistance.
- `efficiency`: optional and must not crowd out correctness.

Weights must sum exactly to 100. The materialized verifier must derive total
reward from node outcomes, not a separate hidden formula.

## Visibility audit

Inspect `bundle-manifest.json` and the actual Agent environment. Confirm:

- reference source is `reference-sidecar-only`;
- site and instance tests are `verifier-only`;
- hidden fixtures are `verifier-only`;
- solution files are `oracle-only`;
- starter and permitted evidence are `agent-public`;
- no build layer, copied archive, cache, log, or artifact leaks hidden content.

Audit network access as well as files. Candidate must not route requests through
the reference sidecar.

## Calibration record

For a release candidate, preserve:

- exact Harbor/provider and Compose versions;
- materialized bundle hash;
- Agent/runtime image identifiers;
- NOP and oracle run identifiers;
- CTRF and reward files;
- per-node logs and artifacts;
- repeated-run comparison;
- visibility/network audit;
- human browser review.

Update the instance thresholds only from reviewed evidence. Do not tune a
threshold merely to pass a broken verifier or weak oracle.

## Expansion checklist

Before accepting a new instance:

- explain its new capability coverage relative to existing instances;
- reuse a site contract when the reference runtime is genuinely the same;
- keep instance-specific goals out of shared site code;
- include failure and boundary states, not only happy paths;
- verify deterministic reset and state isolation;
- run corpus-wide validation after any shared site change;
- keep generated `harbor-dist/` bundles out of Git unless a release policy
  explicitly freezes them.
