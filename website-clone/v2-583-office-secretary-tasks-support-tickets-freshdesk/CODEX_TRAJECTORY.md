# Codex trajectory

## Scope

- Source task: V2 583, office-secretary-tasks/customer-support, Freshdesk.
- Owned replica: `website-clone/v2-583-office-secretary-tasks-support-tickets-freshdesk/`.
- Owned dev task: `tasks/dev/dev-115-freshdesk-invoice-dispute-ticket/task.json`.
- Canonical port: 8132.
- Terminal boundary: exact local `POST /api/_/tickets`.

## Inputs read

1. Repository `AGENTS.md`, root task schema/context, ClawBench-Pro website-clone and task-format documentation, source V2 task 583, and the assigned Alex Green fixture.
2. Recent complete replicas for Trustpilot, Doodle, TripIt, and Indeed, focusing on strict terminal journaling, SQLite/UI agreement, account boundaries, responsive state coverage, and Playwright evidence.
3. Freshworks' public trial and dashboard pages plus Freshdesk's current first-party ticket fields, ticket list, ticket detail, and automation documentation.
4. Freshdesk's published API ticket representation for requester, subject, description, status, priority, source, group, and responder semantics.

## Contract decisions

1. The source terminal path is retained byte-for-byte as `/api/_/tickets`; only the host and canonical local port differ.
2. The deterministic JSON body uses public Freshdesk ticket property names. Open is `2`, High is `3`, and Phone is `3`. The latter represents a ticket created by an agent for a requester.
3. The description is fixed in dev-115 because the source requests a meaningful discrepancy but provides no text. An exact local sentence makes the matcher falsifiable while retaining the requested billing discrepancy.
4. Alex Green requester `1001`, Test Agent responder `2002`, and Support group `3001` are explicit local fixture IDs. The task-visible labels and database values agree.
5. The source task's free Sprout requirement is preserved even though current Freshworks public acquisition emphasizes a 14-day trial. This is documented as a task-era compatibility choice.

## Implementation

1. Added a dependency-free `ThreadingHTTPServer` with secure static resolution, same-origin CSP, size/content-type checks, stable local sessions, and SQLite WAL persistence.
2. Added tables for sessions, signup drafts, accounts, workspaces, ticket drafts, tickets, lifecycle events, terminal/update request journal, and explicit boundary events.
3. Added strict registration and local verification, Sprout setup, dashboard, inbox, ticket creation/autosave, detail, edit, resolve/reopen, logout/login recovery, list failure/retry, terminal failure/retry, validation, duplicate, malformed, unauthorized, empty, search, missing-ticket, and 404 states.
4. Required the successful terminal payload to match the saved visible draft. Ticket, creation event, and 201 terminal journal row are committed in one SQLite transaction.
5. Added explicit local-only controls for identity/email verification, customer creation, team invitations, help/marketplace, and integrations. Every runtime asset and request remains same-origin.
6. Added a schema-valid dev task with exact body matching for all three supported local hostnames on port 8132.

## Verification target

- Full successful task at 1365x900 and 390x844.
- Exact request URL/method/body/status and direct SQLite ticket, account, workspace, event, and journal evidence.
- Draft and completed-ticket refresh persistence, ticket edit/resolve/reopen, logout/login recovery, and ticket search/filter behavior.
- Required-field, wrong-code, duplicate, malformed JSON, unsupported content type/field, unauthenticated, retryable list/terminal, session isolation, missing API, missing ticket, and general 404 behavior.
- No external requests, page errors, unexpected console errors, broken same-origin assets, horizontal document overflow, or viewport-clipped visible controls.

## Completed verification

- Fresh-database Playwright run: **173/173 checks passed**; the canonical machine-readable report is `verification-report.json`.
- Desktop viewport: 1365x900. Mobile viewport: 390x844.
- Both successful sessions emitted the exact nine-field terminal body, returned 201 after the deliberate local retry check, persisted one matching ticket, one creation event, and one successful terminal journal row, and survived refresh plus logout/login recovery.
- Direct SQLite inspection agreed with the visible draft and ticket detail for account, Sprout workspace, requester, subject, description, status, priority, source, group, agent, type, and terminal payload.
- Recovery checks covered wrong registration state, wrong verification code, invalid workspace, required ticket fields, temporary list and terminal failures, duplicate, unsupported field, malformed JSON, unsupported content type, unauthenticated terminal, isolated session, missing API/ticket, search no-results, and general 404.
- Browser telemetry reported zero external requests, page errors, unexpected console errors, broken local images, clipped visible controls, overflowing button labels, or horizontal document overflow.
