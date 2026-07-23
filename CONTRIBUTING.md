# Contributing offline clones

New clone work should follow the resource-first workflow documented in
[`docs/offline-clone-harness.md`](docs/offline-clone-harness.md):

1. Freeze the site's purpose, core journeys, semantic invariants, and explicit
   non-goals before implementation.
2. Capture a stable source baseline and close the required local resource set.
3. Build and visually verify the frontend route/state matrix before expanding
   backend behavior.
4. Implement only the backend semantics needed by the frozen journeys, with
   server-side validation and deterministic reset behavior.
5. Iterate through evidence-backed functional and visual gates. Reports must
   distinguish directly compared, structural-only, unavailable, and inferred
   states.
6. Keep credentials, user data, browser profiles, runtime databases, and
   generated artifacts out of Git.
7. Document source ownership, redistribution limits, simulations, and known
   fidelity gaps without overstating completion.

Before implementation, register the work in `project/plan.json`. Update status
only when its acceptance/exit criteria are met, attach evidence to every
`complete` item, and record real blockers in `blocked_by`. The lifecycle,
ownership model, definitions of done, and expansion policy are defined in
[`PROJECT.md`](PROJECT.md).

Run before committing:

```bash
clawbench-project validate
ruff check src tests websitebench
python -m pytest tests/project tests/offline_clone tests/harbor tests/viewer -q
python -m pytest materials/amazon/clone/tests -q
```

Full-stack benchmark instances must follow
[`docs/harbor-fullstack-benchmark.md`](docs/harbor-fullstack-benchmark.md).
Keep reusable website contracts under `harbor/sites/`, task-specific overlays
under `harbor/instances/`, and generated Harbor bundles under the ignored
`harbor-dist/`. Browser Use CLI is the Agent exploration path; trusted
Playwright and direct HTTP checks are the formal scoring path.

When the change affects scope, isolation, scoring, authoring layout, release
evidence, or corpus expansion, update the repository-local
`skills/build-offline-site-clone/` workflow in the same change.
