# Task instances

Each direct child is one Agent task:

```text
instances/<instance-id>/
├── instance.yaml
├── instruction.md
├── public/                 # candidate scaffold; must contain run.sh
├── verifier/               # task-specific hidden checks
├── fixtures/hidden/        # task-specific hidden states
└── solution/solve.sh       # private oracle solution
```

Use `clawbench-harbor init-instance` to create the source structure.
`instance.yaml` references a site manifest relative to the `harbor/` root, for
example `sites/example-store/site.yaml`.

Every full-stack instance must declare exact, globally unique nodes in the
`contract`, `api`, `ui`, `visual`, `journey`, and `robustness` groups. API and
UI each require at least two nodes. The generated verifier rejects missing,
extra, duplicate, skipped, or malformed result sets rather than silently
changing the denominator.
