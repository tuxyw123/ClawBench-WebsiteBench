# Northstar Market Candidate Contract

Contract version: `websitebench.candidate.v1`

The candidate may use any implementation language or framework. Evaluation
compares browser behavior and normalized state, not source architecture.

## Required submission layout

```text
/app/
  frontend/
  backend/
  README.md
  Dockerfile
  docker-compose.yml
  .env.example
  scripts/
    seed
    reset
```

`frontend/` and `backend/` must both exist even when a monolithic framework is
used; the README may explain shared or generated code. `scripts/seed` and
`scripts/reset` must be executable, non-interactive, and safe to rerun.

## Startup and ports

From a clean submission, this command must build and start the site:

```bash
docker compose up --build --wait
```

- Public HTTP listens on container port `8080`.
- Benchmark admin HTTP listens on container port `8081` and must not be linked
  from or proxied through the public app.
- `GET http://candidate:8080/healthz` returns HTTP 200 JSON containing
  `{"status":"ok"}` only after the public app can serve requests and its
  persistent store is ready.
- Data that must survive restart is stored below `/data`.
- The container supplies a health check and stops cleanly on SIGTERM.

The candidate compose file must not bind a fixed host port. The benchmark
orchestrator publishes random host ports when needed.

## Runtime environment

The evaluator supplies:

| Variable | Meaning |
| --- | --- |
| `PORT=8080` | Public HTTP port |
| `BENCH_ADMIN_PORT=8081` | Private admin port |
| `BENCH_ADMIN_TOKEN` | Secret required on every admin request |
| `DATA_DIR=/data` | Persistent state directory |
| `MAILBOX_API_URL` | Internal benchmark mailbox delivery API |
| `MAILBOX_DELIVERY_TOKEN` | Bearer token for mailbox delivery |
| `PUBLIC_MAILBOX_URL` | Browser-visible mailbox base URL |
| `PUBLIC_SITE_URL` | Browser-visible site base URL used in email links |
| `BENCH_FIXTURE_DIR=/bench-fixtures` | Read-only fixture directory |
| `BENCH_CLOCK_MODE=controlled` | Requires use of benchmark-controlled time |

The application must accept values different from these defaults.

## Benchmark admin HTTP contract

Every request to port 8081 requires header:

```text
X-Bench-Admin-Token: <BENCH_ADMIN_TOKEN>
```

Missing or incorrect credentials return 404 so the endpoint is not
distinguishable to an untrusted caller. The public listener must return 404 for
all `/__bench/*` paths.

### `GET /__bench/health`

Returns 200 only after the database is ready:

```json
{"schema_version":1,"status":"ok"}
```

### `POST /__bench/reset`

Request body follows `websitebench.reset-request.v1`:

```json
{
  "schema_version": 1,
  "run_id": "run-identifier",
  "seed": 1101,
  "now": "2026-01-15T12:00:00Z",
  "fixture_path": "/bench-fixtures/1101.json"
}
```

The fixture path must resolve inside `BENCH_FIXTURE_DIR`; traversal and symlink
escape are rejected. Reset is transactional and idempotent: it clears existing
application state, loads the selected schema-valid fixture, sets the controlled
clock, and clears sessions. The evaluator resets the external mailbox as a
separate privileged orchestration step. The app returns:

```json
{
  "schema_version": 1,
  "status": "reset",
  "run_id": "run-identifier",
  "seed": 1101,
  "now": "2026-01-15T12:00:00Z"
}
```

### `POST /__bench/clock/advance`

Request body follows `websitebench.clock-advance.v1`:

```json
{"seconds": 300}
```

`seconds` is an integer from 0 through 2,678,400 (31 days). The operation is
atomic and returns the new UTC time:

```json
{"schema_version":1,"now":"2026-01-15T12:05:00Z"}
```

Time never advances automatically while controlled mode is active.

### `GET /__bench/state`

Returns the normalized state described by `websitebench.state.v1`. It includes
catalog inventory, normalized users, carts, orders, token/session counts, clock,
seed, and run ID. It excludes password hashes, raw tokens, session identifiers,
admin credentials, full card values, and CVV. Array order is deterministic.

## Fixture requirements

Fixtures conform to `../../schemas/fixture.schema.json`. Candidate code must not
special-case known seed numbers, fixture paths, product IDs, account emails, or
test order. Hidden fixtures use the same public schema but different values.

## Mail delivery

The benchmark mailbox accepts candidate mail at the manifest's delivery path:

```http
POST ${MAILBOX_API_URL}/api/v1/messages
Authorization: Bearer ${MAILBOX_DELIVERY_TOKEN}
Content-Type: application/json
```

```json
{
  "schema_version": 1,
  "to": "shopper@example.test",
  "subject": "Verify your Northstar Market email",
  "text": "Open the link to verify your account: https://candidate/verify?token=..."
}
```

`to`, `subject`, and plain-text `text` are required. An optional sanitized
`html` string is allowed. The mailbox returns HTTP 202 with a generated message
ID. Delivery authentication failure returns 404. Candidate runtime network
policy permits only this delivery API on the benchmark service network; it
cannot query inboxes and has no internet access. The browser-visible mailbox and
evaluator use the manifest's separate query path.

## Prohibited techniques and hard failures

The following make the run ineligible for a score:

- build, startup, health, or reset failure;
- iframe, proxy, redirect, or runtime request to the reference site;
- runtime internet access or remote screenshot/content service;
- copied private reference source, assets, bundles, cache, or browser profile;
- serving a static façade without a persistent backend for required state;
- exposing the admin token or admin API through public HTTP;
- host networking, privileged containers, host Docker socket, or undeclared
  host filesystem mounts.

Ordinary open-source dependencies fetched during the controlled build phase
are allowed and recorded. Candidate runtime has no package-download access.

## Resource envelope

- clean build: at most 10 minutes;
- final image set: at most 1.5 GiB compressed-size equivalent;
- source submission excluding dependency caches: at most 50 MiB;
- candidate peak memory: at most 1 GiB;
- readiness after already-built image start: at most 60 seconds;
- functional latency target: p95 at most 1 second under 10 concurrent users.

Exceeding a target loses only the efficiency points unless it prevents the
evaluator from completing.
