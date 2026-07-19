# Gate W4 -- Evaluation and release

Status: **implementation complete; container release run blocked by host infrastructure**

Delivered:

- eight paired reference/candidate journeys covering catalog, account
  lifecycle, sessions, password reset, cart merge, checkout, orders, isolation,
  cancellation, and inventory;
- hidden seeds `9101`--`9105`, concurrency seed `9199`, multi-account checks,
  exact boundary probes, and 15 robustness groups;
- 12 responsive visual checkpoints using masked SSIM, edge, color, visible-text,
  and semantic-geometry signals;
- weighted scoring: visual 20, interactions 20, journeys 40, robustness 15,
  efficiency 5, with journey terminal caps and hard-failure zeroing;
- runtime reference/internet request detection, resource/latency measurement,
  schema-valid `websitebench.result.v1`, and an actionable Markdown failure
  report;
- a 12-family controlled failure corpus manifest for evaluator calibration.

Local acceptance evidence:

- `42 passed` for `tests/web2code`;
- Ruff passes for the orchestrator, services, reference, evaluator, and tests;
- all Python sources compile;
- public schemas and Compose trust topology validate;
- a full pilot dry run successfully produced a sanitized run envelope;
- a host-process paired calibration using two independent reference databases,
  the mailbox, Chromium, and the complete private evaluator scored `100 / 100`:
  all eight journeys, all 15 robustness groups, all 12 visual checkpoints, and
  all five efficiency targets passed with no hard failure. Its reproducible
  summary is stored at `judge/calibration/reference-identity.json`.

Outstanding release evidence:

The current execution host contains only a Docker client. It has no Docker
daemon socket, no `dockerd`/`containerd` binary, and no Compose v2 plugin.
Therefore images cannot be built and the browser/evaluator containers cannot be
started here. This is an infrastructure error, not a candidate score. The CLI
now fails preflight with a specific daemon/Compose diagnostic. Run the command
in this directory's README on a Docker-capable host to produce the first full
end-to-end `evaluation-result.json` and failure report.
