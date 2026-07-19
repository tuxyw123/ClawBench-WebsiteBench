# Idealist source-task replica

This folder implements ClawBench V2 source task 776 as dev task 118. It is a
task-scoped offline replica for finding a full-time nonprofit Program Manager
job in Washington, DC, establishing the assigned Alex Green applicant profile,
and completing a local application.

## Run

From the ClawBench repository root:

```bash
python3 website-clone/v2-776-nonprofit-charity-volunteer-signup-idealist/server.py \
  --host 0.0.0.0 --port 8135
```

Host URL: `http://127.0.0.1:8135/`

Container URL: `http://host.docker.internal:8135/`

Runtime state is stored in ignored `idealist.sqlite3`. Pass `--db` to use an
isolated database.

## Terminal and application proof

Source task 776 ends on `POST /data/userdashboard/missing-info`. The local dev
matcher preserves that exact method and path on `localhost`, `127.0.0.1`, and
`host.docker.internal`, port 8135, with an exact six-field Alex Green JSON body.

The endpoint remains a profile-completeness operation. Review first saves the
exact application draft; submit stages it as `PENDING_PROFILE`; the terminal
profile operation atomically marks the account complete and the staged
application `SUBMITTED_LOCALLY`. The judge requires all of these linked facts,
so observing the source terminal request alone cannot pass the task.

## Modeled behavior

- Idealist-shaped search, filters, result cards, listing detail, registration,
  sign-in, applicant profile, assigned resume, review, completion, and My
  applications.
- Deterministic Washington, DC fixtures with Dumbarton Arts & Education's
  Program Manager role as the sole exact filtered result.
- Strict application and profile payloads, a substantive tailored cover letter,
  current-draft enforcement, refresh persistence, sign-out/sign-in recovery,
  duplicate protection, malformed-body handling, retryable search errors,
  session isolation, empty results, local boundaries, and 404 responses.
- SQLite WAL persistence plus a request journal, search/view history, account,
  resume, draft, and application rows exposed through `/api/state`.
- Same-origin static assets and fetches under a restrictive CSP.

## Verify

Start the server against a fresh database, then run:

```bash
python3 website-clone/v2-776-nonprofit-charity-volunteer-signup-idealist/tools/verify_task.py \
  --clone http://127.0.0.1:8135
```

The verifier checks the schema and matcher, exact terminal request, full UI and
SQLite state, journal agreement, persistence and auth recovery, negative cases,
desktop 1365x900 and mobile 390x844 rendering, same-origin networking, console
and page errors, assets, nonblank pixels, controls, and horizontal overflow.

No request reaches Idealist, Dumbarton Arts & Education, an employer, email
provider, file host, identity provider, analytics service, or ad network. No
binary resume is uploaded and no real application is submitted.
