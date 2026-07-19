# Gate W1 -- Protocol Freeze

Status: **approved**

Approval record: the user explicitly approved Gate W1 before authorizing
automatic progress through the remaining stages.

The following public contracts are frozen by this gate:

- `public/PRD.md`
- `public/candidate-contract.md`
- `public/manifest.yaml`
- `public/visual-checkpoints.json`
- `public/public-smoke-cases.json`
- `public/scoring.json`
- `../schemas/site-manifest.schema.json`
- `../schemas/fixture.schema.json`
- `../schemas/admin-contract.schema.json`
- `../schemas/report.schema.json`

## Approval checklist

- [x] Every hidden functional rule is declared in the PRD or normally
  observable through the reference browser UI.
- [x] Cart merge, registration throttle, token/session boundaries, tax,
  shipping, checkout idempotency, inventory transaction, and cancellation
  boundaries are unambiguous.
- [x] Candidate output, health, reset, clock, state, and mailbox contracts are
  implementable without private source access.
- [x] Core and Human+Agent budgets are accepted.
- [x] Visual checkpoints and scoring weights are accepted.
- [x] Seed families and concurrency fixture purpose are accepted.
- [x] Hard-failure and anti-cheat conditions are accepted.

Approval authorizes W2 reference implementation. Any material contract change
after approval increments `site_version` and returns to W1 review.
