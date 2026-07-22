# Amazon worked scope for the offline-clone harness

This directory is the machine-readable acceptance boundary for the finished
local Amazon shopping clone. It is deliberately narrower than Amazon.com.
`purpose.json` freezes the shopping mainline and the P0/P1/P2/omit cut;
`routes.json` and `checkpoints.json` freeze route × state × viewport rows;
`journeys.json` keeps success, failure, and recovery distinct; `coverage.json`
keeps all denominators independent; and `claims.jsonl` binds headline counts to
dated evidence or local semantic tests.

The adapter must never convert these true but different statements into one
percentage: 191 known/reachable products, 14 rich PDPs (11 backed by the direct
per-product capture set), 49 purchasable ASINs, 13 source-review-backed
products, and 39 comparable products. Resource reporting is also split: 452
scoped current/bounded source records, 454 required logical runtime paths after
the current nav alias and historical T7 alias are declared, and 454 physical
runtime files with no undeclared serving-only residue. The quarantined legacy
archive is outside runtime and outside required evidence closure. The clone is
an offline, safe local simulation: no test pass means a
real charge, message delivery, fulfillment action, or whole-site fidelity.

`materials/amazon/FRONTEND_ROUTE_MATRIX.md` is retained only as a historical
implementation/backlog aid. It is not adapter acceptance source-of-truth; the
frozen files in this directory together with `clone.yaml` define acceptance.
