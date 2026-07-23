# Site baselines

Each direct child is one reusable offline-site baseline:

```text
sites/<site-id>/
├── site.yaml
├── public/                 # Agent-visible contracts, never reference source
├── reference/              # private sidecar context; Dockerfile + run.sh
├── verifier/               # trusted site-wide API/Playwright evaluator
│   └── run.py
├── fixtures/hidden/        # reset states and evaluator-only scenario data
└── oracle/                 # private calibration helpers
```

Use `clawbench-harbor init-site` to create this structure. Ten instances for the
same site should normally share one site baseline rather than copy the verifier
ten times. Instance-specific checks belong under the instance overlay.

The generated Agent Dockerfile copies only `environment/seed/`; it never copies
the sibling `environment/reference/` build context. The Compose `reference`
service is reachable by network but the `main` container has neither its files
nor the Docker socket.

`site.yaml` is validated by
`websitebench/schemas/harbor-site.schema.json`. Its visibility roots must be
disjoint and may not contain symbolic links, junctions, reparse points, or hard
links.
