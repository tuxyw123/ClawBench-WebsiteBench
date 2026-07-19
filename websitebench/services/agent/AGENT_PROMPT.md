# Northstar Market candidate build

Build the complete candidate described by `/task/task.json` and the public files
under `/task/public`. Write all submission files beneath `/workspace/candidate`.

You may continuously explore the reference and local mailbox with the
`websitebench` MCP browser tools. These are the only permitted way to access
them. Do not use shell HTTP clients, DNS tools, raw sockets, DevTools, browser
profiles, caches, downloads, page-source extraction, or copied target assets.

You may use normal shell and coding tools inside `/workspace/candidate`. Use
`candidate_build` for budgeted clean builds and `candidate_preview_status` plus
the BrowserUse candidate target for preview checks. Do not attempt to reach the
container engine or control services directly.

Requirements:

- Explore before choosing the final visual design and interaction details.
- Implement a real persistent backend and every behavior in the public PRD.
- Implement the public/admin ports, fixture Reset, controlled Clock, normalized
  State, local mailbox delivery, Seed/Reset scripts, health checks, Dockerfile,
  and Compose contract.
- Keep all candidate state under `/data` and all target-independent source under
  `/workspace/candidate`.
- Test desktop and mobile flows, error states, refresh persistence, multi-account
  isolation, idempotency, and concurrent stock behavior.
- Do not hard-code public seed values; hidden fixtures follow the same schema.
- Stop only when the required layout is complete and the latest clean build
  succeeds, or when the task budget is exhausted.

