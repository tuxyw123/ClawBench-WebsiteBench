# Limitations and safety boundaries

- This is a deterministic task-scoped replica for source V2 task 86, not a general Greenhouse, CodePath, recruiting, identity, or document-processing service.
- The source attachment identifies CodePath job `4526154007`, but that live listing is no longer open. The replica preserves its job ID, title, company, attached URL shape, remote region, and plausible public engineering-role detail rather than redirecting to current inventory.
- The assigned resume is represented as `Alex_Green_Resume.pdf` plus a same-origin readable preview generated from the benchmark Alex Green profile. No binary upload, OCR, malware scan, document conversion, or real storage provider is involved.
- The shared profile contains no phone or LinkedIn URL. Those fields are not invented or made required. The deterministic required fields are limited to facts present in the assigned profile and task-relevant authorization answers.
- The application is tailored to the role's Canada-eligible remote location. Alex is a Canadian citizen, so Canadian work authorization is `true` and sponsorship is `false`. The replica does not claim U.S. authorization.
- Adjacent roles, board search, company context, application status, and job alert/MyGreenhouse boundaries exist to support navigation and recovery, but do not model production ranking, accounts, recruiting stages, notifications, demographic surveys, or general applications.
- A local test-only reset and fail-next endpoint requires `X-Replica-Test: 1`; these support deterministic verification and have no external effect.
- All durable state is confined to the selected SQLite file. No application, resume, email, identity, employer, analytics, advertising, captcha, or Greenhouse request leaves the configured local origin.
- CodePath and Greenhouse names and visual references identify the publicly observable task surface and do not imply affiliation or endorsement.
