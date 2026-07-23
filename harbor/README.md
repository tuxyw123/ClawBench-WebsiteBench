# Harbor full-stack benchmark authoring

This directory is the normalized authoring root for browser-driven, full-stack
offline-site reconstruction tasks. It stores reusable site contracts separately
from task instances:

```text
harbor/
├── sites/<site-id>/site.yaml
├── instances/<instance-id>/instance.yaml
└── README.md
```

Generated Harbor bundles do **not** belong here or in Git. Materialize them into
the ignored `harbor-dist/` directory. The bundle uses Harbor's native
`environment/docker-compose.yaml`: `main` is the Agent container and
`reference` is a network-only sidecar whose source is never copied into
`main`.

The intended execution split is strict:

- the Agent uses Browser Use CLI to inspect the browser-only reference and to
  self-check the candidate in `/app/repo`;
- the formal verifier uses trusted Playwright and direct HTTP checks;
- a human reviewer may open reference and candidate URLs side by side;
- reference source, hidden fixtures, verifier code, and oracle material never
  enter the Agent image.

Create and review a Harbor-native bundle:

```bash
clawbench-harbor init-site \
  --site-dir harbor/sites/example-store \
  --site-id example-store \
  --display-name "Example Store"

clawbench-harbor init-instance \
  --instance-dir harbor/instances/example-store-rebuild \
  --instance-id example-store-rebuild \
  --site-manifest sites/example-store/site.yaml \
  --author-name "Benchmark Team" \
  --author-email benchmark@example.com

clawbench-harbor validate \
  --instance harbor/instances/example-store-rebuild

clawbench-harbor materialize \
  --instance harbor/instances/example-store-rebuild \
  --out harbor-dist/example-store-rebuild
```

Before release, run the generated task with Harbor's `nop` and `oracle` agents,
repeat the formal verifier, and perform a human browser review. Structural
validation alone is not a release claim.

The complete authoring, verifier, scoring, and release standard is in
[`docs/harbor-fullstack-benchmark.md`](../docs/harbor-fullstack-benchmark.md).
