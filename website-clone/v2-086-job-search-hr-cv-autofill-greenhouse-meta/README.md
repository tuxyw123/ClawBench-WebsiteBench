# Greenhouse / CodePath application replica

This directory implements the task-scoped offline replica for source V2 task 86. It preserves the attached CodePath job URL semantics for job `4526154007` and models the public first-party path from CodePath company and job-board pages through the exact Senior Software Engineer listing, resume autofill, application review, local submission, confirmation, and status.

## Run

From the repository root:

```bash
python3 website-clone/v2-086-job-search-hr-cv-autofill-greenhouse-meta/server.py \
  --host 0.0.0.0 --port 8134
```

Open `http://127.0.0.1:8134/codepath/jobs/4526154007`. Container tasks use `http://host.docker.internal:8134/codepath/jobs/4526154007` from the copied `job_links.json` attachment.

The default database is `greenhouse.sqlite3` in this directory. Pass `--db /tmp/greenhouse-test.sqlite3` for an isolated run.

## Terminal contract

The review page emits one exact JSON request:

```http
POST /v1/boards/codepath/jobs/4526154007
Content-Type: application/json
```

Its 15-field payload contains the assigned Alex Green identity, parsed resume descriptor, current role, highest degree, experience band, Canadian work-authorization answer, sponsorship answer, future-opportunity preference, and explicit review consent. The authoritative exact body is in `tasks/dev/dev-117-greenhouse-codepath-application/task.json` and `server.py` as `EXPECTED_PAYLOAD`.

The dev matcher accepts only `localhost`, `127.0.0.1`, and `host.docker.internal` on port `8134`, with the exact source-style Greenhouse board path. A successful write requires a matching step-3 reviewed draft. The application row and successful terminal journal row commit atomically; duplicates, stale drafts, malformed or unsupported bodies, temporary failures, and session crossover are rejected and journaled.

## Modeled surfaces

- CodePath company context, searchable Greenhouse job board, adjacent roles, and exact job listing;
- source-shaped responsive listing, application, review, confirmation, privacy, status, local boundary, empty, and 404 pages;
- assigned `Alex_Green_Resume.pdf` representation with visible extracted identity, experience, education, and skills;
- required-field validation, autosave, draft refresh restoration, temporary submission error/retry, duplicate protection, malformed and unsupported requests, missing job/API 404s, and browser-session isolation;
- SQLite WAL persistence for sessions, listing views, drafts, applications, request journal, and local-only boundary events;
- strict same-origin CSP and bundled assets with no runtime third-party traffic.

## Verify

Start the server on a fresh database, then run:

```bash
python3 website-clone/v2-086-job-search-hr-cv-autofill-greenhouse-meta/tools/verify_task.py \
  --clone http://127.0.0.1:8134 \
  --db /tmp/greenhouse-verify.sqlite3
```

The verifier validates the dev task schema and copied attachment, drives desktop `1365x900` and mobile `390x844` flows, captures the terminal request, checks refresh and retry behavior, queries SQLite directly, and audits network, assets, page/console errors, visible-control clipping, and horizontal overflow. It writes ignored screenshots under `.verification-artifacts/` and `verification-report.json`.

See `LIMITATIONS.md`, `ASSET_ATTRIBUTION.md`, and `CODEX_TRAJECTORY.md` for scope and provenance.
