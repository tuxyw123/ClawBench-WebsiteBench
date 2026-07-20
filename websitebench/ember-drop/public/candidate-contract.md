# Candidate contract — Ember Drop

Build a persistent full-stack application implementing every public rule. No reference proxying or external network dependency is allowed.

## Runtime and interaction contract

- Serve the browser application on `PORT` (8080) and the private deterministic control plane on `BENCH_ADMIN_PORT` (8081).
- Implement the routes in `manifest.yaml` with normal HTML forms for registration, login, reset, cart add/update, checkout, order detail, and cancellation.
- Cart add accepts `product_id`, `quantity`, and `return_to`; checkout accepts `idempotency_key`, test card fields, and—when applicable—`store` and `slot`.
- Deliver verification/reset links only to `MAILBOX_API_URL` with `MAILBOX_DELIVERY_TOKEN`.
- Persist accounts, sessions, tokens, carts, reservations, inventory/capacity, idempotency records, and orders below `DATA_DIR` across restarts.

## Private deterministic control plane

Require `X-Bench-Admin-Token` for `POST /__bench/reset`, `GET /__bench/state`, and `POST /__bench/clock/advance`. Reset accepts `run_id`, `seed`, `now`, and an evaluator-provided fixture path. State returns normalized entities/resources plus the loaded public `policy_profile`; clock advance accepts non-negative `seconds`. The public port must return 404 for every `/__bench/*` path.
