# Contributing benchmark items

New WebsiteBench items must preserve the same public/private boundary as
Northstar Market:

1. Put agent-visible contracts, PRD, smoke cases, fixtures, scoring, and visual
   checkpoints under `websitebench/<site>/public/`.
2. Keep the reference implementation and hidden judge outside `public/`.
3. Declare every public artifact in `public/manifest.yaml` and validate it
   against `websitebench/schemas/site-manifest.schema.json`.
4. Provide deterministic seed/reset behavior, health checks, controlled time,
   account isolation, and concurrency-safe state transitions.
5. Add contract, behavior, topology, scoring, and report tests.
6. Do not expose reference source, hidden fixtures, container sockets, raw
   network bodies, browser profiles, or per-run secrets to the Agent.
7. Version behavior changes and recalibrate reference identity before release.

Run before committing:

```bash
clawbench-web2code validate --site northstar-market
ruff check src tests websitebench
python -m pytest tests/web2code tests/viewer -q
```

The complete protocol and gate history are in `WebsiteBench.md` and
`websitebench/northstar-market/GATE_W*.md`.

