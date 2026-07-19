# Codex trajectory

## Scope

- Source task: V2 86, job-search-hr / cv-autofill / Greenhouse-CodePath.
- Owned replica: `website-clone/v2-086-job-search-hr-cv-autofill-greenhouse-meta/`.
- Owned dev task: `tasks/dev/dev-117-greenhouse-codepath-application/`.
- Canonical port: `8134`.
- Exact target: CodePath Senior Software Engineer, job `4526154007`.
- Terminal boundary: `POST /v1/boards/codepath/jobs/4526154007`.

## Inputs read

1. Repository `AGENTS.md`, root and ClawBench-Pro task schemas, contributor guidance, source V2 task 86, and source `extra_info/job_links.json`.
2. The shared Alex Green personal-information fixture, including identity, Toronto address, education, work history, technical skills, certifications, and professional summary.
3. Recent complete replicas for Indeed and Freshdesk, focusing on exact terminal bodies, local attachment rewriting, SQLite/UI agreement, browser-session isolation, and Playwright evidence.
4. Public CodePath Greenhouse pages to confirm the current first-party shape: company job board, exact listing, Apply action, required identity/location/resume fields, employer questions, review/consent, and confirmation semantics.

## Contract decisions

1. The source attachment URL `https://job-boards.greenhouse.io/codepath/jobs/4526154007` becomes `http://host.docker.internal:8134/codepath/jobs/4526154007`; board token, job ID, company, title, path shape, and one-link attachment semantics remain intact.
2. The terminal uses the allowed source-style board API path `/v1/boards/codepath/jobs/4526154007`, with only the host and port localized.
3. Exact payload fields are limited to task-relevant facts available in the assigned profile. Phone and LinkedIn are omitted because the benchmark fixture does not provide them.
4. Canadian authorization is true and sponsorship is false because Alex is a Canadian citizen and the represented role accepts Canada-based remote applicants.
5. `Alex_Green_Resume.pdf` is a visible assigned-profile representation with a same-origin preview and parsed fields. It never becomes a real upload.

## Implementation

1. Added a standard-library threaded server with secure static resolution, same-origin CSP, JSON size/content-type validation, canonical routes, and true 404 pages.
2. Added SQLite WAL tables for sessions, exact listing views, application drafts, local applications, request journal entries, and local boundary events.
3. Added CodePath company, Greenhouse board, exact listing, parsed-resume application, review, confirmation, application status, privacy, boundary, error, empty, and recovery surfaces.
4. Required the successful terminal body to equal the exact reviewed step-3 draft. Application and successful journal evidence are atomic; temporary failure, mismatch, stale draft, and duplicate attempts are journaled without duplicate application rows.
5. Added a schema-valid dev-117 task with the copied/localized attachment and an exact matcher for the three supported local hostnames on port 8134.

## Verification target

- Full successful task at `1365x900`, plus mobile listing/application/review at `390x844`.
- Exact URL, method, content type, payload, confirmation/state, journal, and direct SQLite agreement.
- Resume preview and visible extracted identity, employment, education, experience, and required employer answers.
- Validation, draft refresh, temporary terminal retry, malformed JSON, unsupported content type/fields, stale/direct terminal, duplicate, session isolation, missing job/API, and real 404 recovery.
- No external traffic, broken same-origin assets, page errors, unexpected console errors, viewport-clipped controls, or horizontal overflow.

## Safety

No real Greenhouse, CodePath, employer, email, identity, captcha, document upload, analytics, advertising, or recruiting effect occurs. Completion is one browser-session-scoped SQLite application row.
