# Codex trajectory

## Intake

- Read repository agent guidance, replica documentation, the V2 task schema,
  source task 776, assigned Alex Green data, and recent job/nonprofit replicas.
- Confirmed the source terminal matcher is only
  `POST www.idealist.org/data/userdashboard/missing-info`.
- Checked current public Idealist search and listing semantics for Program
  Management jobs in Washington, DC and a Dumbarton Arts & Education Program
  Manager listing.

## Design

- Chose deterministic exact filters: `Program Manager`, `Washington, DC`,
  `Full Time`, and `Nonprofit`.
- Preserved the source endpoint as profile completion instead of relabeling it
  as application submission.
- Added a reviewed draft and staged application before terminal profile
  completion. The terminal transaction completes both the profile and the
  already-staged application, preserving user-visible continuity and durable
  proof.
- Scoped all state to secure random local sessions and stored durable evidence
  in SQLite WAL tables plus a request journal.

## Implementation

- Built search/list/detail, account/sign-in, profile/resume, application/review,
  completion, My applications, local boundary, empty, error, and 404 surfaces.
- Added strict validation, malformed request responses, stale-draft checks,
  duplicate prevention, auth recovery, refresh persistence, retryable search,
  and local-only CSP/security headers.
- Added dev task 118 with exact local matcher, ordered step matchers, and a judge
  rubric requiring full application evidence beyond the source terminal call.

## Verification evidence

- Ruff, Python compilation, Node syntax checks, and task-schema validation pass.
- `PYTHONPATH=src python3 -m pytest tests/test_task_schema.py -q` passes 6
  tests with 2 environment-appropriate skips.
- A fresh-database Playwright run passes 141/141 assertions and writes 12
  screenshots plus `verification-report.json`.
- Verified exact terminal capture, `/api/state` and SQLite agreement, desktop
  1365x900, mobile 390x844, persistence, auth recovery, malformed and duplicate
  handling, retry and empty states, session isolation, 404s, same-origin traffic,
  no page/console errors, nonblank pixels, loaded assets, controls in canvas, and
  zero horizontal overflow.

## Safety boundary

The replica never contacts Idealist, Dumbarton Arts & Education, an employer,
email provider, upload/file host, identity provider, analytics service, or ad
network. The resume and application are local representations with no external
effect.
