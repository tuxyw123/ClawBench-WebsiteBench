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

Run before committing:

```bash
ruff check src tests websitebench
python -m pytest tests/offline_clone tests/viewer -q
python -m pytest materials/amazon/clone/tests -q
```
