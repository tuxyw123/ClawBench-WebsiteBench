# Gate W2 -- Reference behavior

Status: **implemented and auto-approved**

Authorization record: after W1 approval, the user instructed the Agent to enter
M2 and automatically continue through all later stages.

Delivered:

- dual-listener private reference service with public and authenticated admin
  ports;
- SQLite persistence, transactional fixture reset, normalized state, and a
  deterministic controlled clock;
- registration, verification, login/logout, password reset, session expiry,
  guest/account cart merge, checkout, idempotency, order isolation,
  cancellation, and atomic inventory;
- persistent browser-visible local mailbox with a separate private reset API;
- deterministic 48-product fixtures for public, hidden, and concurrency seeds;
- reference Dockerfile, seed/reset scripts, health checks, and business-level
  regression tests.

Acceptance evidence:

- reference route smoke checks completed with the FastAPI test client;
- exact throttle, token, session, cancellation, payment, cart, account
  isolation, persistence-state, and stock-one concurrency boundaries are
  covered by `tests/web2code/test_northstar_reference.py`;
- the combined Web2Code2Web suite currently passes 42 tests.
