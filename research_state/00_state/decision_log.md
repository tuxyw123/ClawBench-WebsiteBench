# Decision log

## D001 — Registry owns split and runtime identity

- Date: 2026-07-20
- Decision: `websitebench.registry.v1` is the only `site_id → driver` and `family_id → split` authority. Variant data cannot declare `split`.
- Consequence: Related Commerce variants cannot silently cross train/validation/test boundaries; every run freezes a digest-addressed trusted snapshot.

## D002 — Facts and attribution precede scoring

- Date: 2026-07-20
- Decision: Judges emit `websitebench.facts.v1`; the host alone scores and writes result v1. Attempt attribution is stored separately as `scored`, `candidate_failed`, `evaluator_failed`, or allowlisted `infrastructure_error`.
- Consequence: Missing/invalid facts cannot masquerade as a candidate score; schema-valid facts remain scoreable even after a non-zero evaluator exit.

## D003 — Use three mechanism-diverse variants

- Date: 2026-07-20
- Decision: C001 uses Foundry wholesale pricing/quantity, Ember reservation/final-sale, and Harbor store/slot atomicity. Branding/catalog-only variants fail compilation.
- Consequence: The small matrix retains the business-state mechanisms required by the hypothesis.

## D004 — One repetition is a resolution probe

- Date: 2026-07-20
- Decision: Run one clone per site/effort configuration, explicitly not estimating model variance.
- Consequence: A passing C001 justifies repetitions and more variants; it does not support variance or confidence-interval claims.

## D005 — Amazon remains calibration only

- Date: 2026-07-20
- Decision: Amazon-136 is never registered, never receives `/100`, and never enters the Commerce leaderboard.
- Consequence: Its four records diagnose the legacy browser harness without contaminating official WebsiteBench results.

## D006 — Defer checkpointed HITL

- Date: 2026-07-20
- Decision: C001 creates Core jobs only. Genuine Human+Agent execution follows Issue #8 under a new experiment digest after Core stability.
- Consequence: Automated prompting cannot be reported as HITL evidence.
