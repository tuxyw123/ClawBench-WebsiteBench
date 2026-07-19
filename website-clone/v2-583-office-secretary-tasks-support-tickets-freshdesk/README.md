# Freshdesk invoice dispute task replica

This directory contains the task-scoped offline Freshdesk replica for V2 source task 583. It models the normal first-party path from local free Sprout registration through workspace setup, the agent dashboard, and one assigned high-priority billing ticket. It is not a general Freshdesk replacement.

## Run

From the ClawBench repository root:

```bash
python3 website-clone/v2-583-office-secretary-tasks-support-tickets-freshdesk/server.py \
  --host 0.0.0.0 --port 8132
```

Open `http://127.0.0.1:8132/`. The default database is `freshdesk.sqlite3` in this directory. Use `--db /tmp/freshdesk-test.sqlite3` for isolated verification.

The assigned local identity is Alex Green (`alex.green.uoft@clawbench.cc`). The verifier uses password `Freshdesk!42`; the local verification code shown by the UI is `246810`. Neither registration nor verification contacts an identity or email service.

## Terminal contract

The completed form emits exactly:

```http
POST /api/_/tickets
Content-Type: application/json

{"requester_id":1001,"subject":"Invoice Dispute - Order #10042","description":"The invoice for Order #10042 includes a duplicate $125.00 service charge. Please review the billing discrepancy and issue a corrected invoice.","status":2,"priority":3,"source":3,"group_id":3001,"responder_id":2002,"type":"Billing"}
```

The method and path preserve the source evaluator's `freshdesk.com/api/_/tickets` boundary. Numeric status and priority values follow Freshdesk's published ticket API semantics: Open is `2` and High is `3`. Phone source `3` represents an agent-created ticket. The stable requester, Support group, and Test Agent IDs are explicit local fixtures.

The server requires an authenticated, verified Alex Green session with a completed Sprout workspace. The terminal body must match the visible SQLite draft exactly. Unsupported fields, malformed bodies, invalid fixture IDs, mismatched state, temporary errors, and duplicates are journaled and rejected; successful ticket insertion, creation event, and terminal journal entry commit atomically.

The companion task is `tasks/dev/dev-115-freshdesk-invoice-dispute-ticket/task.json`. Its exact matcher accepts only `localhost`, `127.0.0.1`, and `host.docker.internal` on canonical port 8132.

## Modeled surfaces

- assigned-identity registration, local email-code verification, Sprout workspace setup, logout and login recovery;
- agent dashboard, ticket inbox, search and status filters, ticket creation, autosaved draft restoration, detail, edit, resolve, and reopen;
- Alex Green requester, Pinecrest Technologies customer context, Support group, and Test Agent assignment fixtures;
- required-field and exact task validation, duplicate protection, temporary list and terminal failure retry, unauthorized session handling, malformed/unsupported API errors, empty state, search no-results, missing ticket, and general 404 recovery;
- local-only identity, email, customer, team invitation, help, marketplace, and integration boundaries;
- SQLite WAL persistence for accounts, workspaces, drafts, tickets, ticket events, request journal, and boundary events;
- responsive source-shaped layouts at 1365x900 and 390x844 with bundled same-origin assets and no runtime network dependency.

## Verify

Start the server against a fresh database, then run:

```bash
python3 website-clone/v2-583-office-secretary-tasks-support-tickets-freshdesk/tools/verify_task.py \
  --clone http://127.0.0.1:8132 \
  --db /tmp/freshdesk-verify.sqlite3
```

The verifier validates the dev task schema and source-task linkage, drives the complete task at desktop and mobile sizes, captures the exact request, inspects direct SQLite evidence, reloads persistent state, exercises lifecycle and recovery behavior, and checks page errors, unexpected console errors, external requests, same-origin assets, control clipping, and horizontal overflow. It writes `verification-report.json` and screenshots under `.verification-artifacts/`; both paths are ignored.

Current canonical result: **173/173 checks passed** at 1365x900 and 390x844 on a fresh database.

## State endpoints

- `GET /api/health`: process health and canonical port.
- `GET /api/bootstrap` and `GET /api/state`: complete current-session evidence.
- `POST /api/signup/draft`: local signup/workspace draft.
- `POST /api/auth/register`, `/api/auth/verify`, `/api/auth/login`, `/api/auth/logout`: local account boundary.
- `POST /api/workspaces`: free Sprout workspace creation.
- `GET /api/_/tickets` and `GET /api/_/tickets/{id}`: ticket list/detail.
- `POST /api/ticket-draft`: task-visible autosave.
- `POST /api/_/tickets`: strict source-path terminal write.
- `PATCH /api/_/tickets/{id}` and `POST /api/_/tickets/{id}/reopen`: task-adjacent lifecycle.
- `POST /api/boundary`: auditable local-only boundary event.

See `LIMITATIONS.md` and `ASSET_ATTRIBUTION.md` for scope and provenance.
