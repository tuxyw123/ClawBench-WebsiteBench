# ClawBench WebsiteBench

This repository contains the Amazon offline-clone implementation, its frozen
source evidence, and reusable infrastructure for building and reviewing future
offline website clones.

The former synthetic benchmark, evaluator stack, and generated publication were
removed. The retained code is centered on purpose-driven source capture,
resource closure, frontend fidelity, backend semantics, iterative verification,
and corpus review.

## Repository map

| Path | Purpose |
| --- | --- |
| `materials/amazon/` | Amazon source evidence, scope contracts, runnable offline clone, and verification tooling |
| `src/clawbench/offline_clone/` | Reusable offline-clone harness and validators |
| `tests/offline_clone/` | Harness regression tests |
| `src/clawbench/harbor/` | Harbor authoring schemas, scaffolds, bundle materializer, and trusted verifier templates |
| `harbor/` | Normalized site/instance authoring root; generated bundles stay outside Git |
| `tests/harbor/` | Harbor manifest, isolation, materialization, CLI, and scoring regression tests |
| `project/plan.json` | Machine-readable project status, milestones, backlog, risks, decisions, and release gates |
| `src/clawbench/project/` | Project-plan validation, status, and release-gate CLI |
| `skills/build-offline-site-clone/` | Versioned website-clone and benchmark-governance skill package |
| `src/clawbench/viewer/` | Authenticated corpus-QA and result viewer |
| `tests/viewer/` | Viewer discovery, security, reviews, evidence, and browser tests |
| `tasks/` | Amazon task plus compact legacy Viewer fixtures |
| `website-clone/` | Legacy compatibility clones used by Viewer regression tests |
| `websitebench/corpora/claw-bench-v2/` | Portable 129-task / 61-platform live-site inventory used by the clone expansion prompt |
| `websitebench/schemas/` | Shared offline-clone, Viewer, and result schemas |
| `docs/` | Clone methodology, Amazon case study, and Viewer operation |

## Install and validate

Python 3.11 or newer is required.

```bash
python -m pip install -e '.[dev]'
python -m playwright install chromium
ruff check src tests websitebench
python -m pytest tests/offline_clone tests/harbor tests/viewer -q
python -m pytest materials/amazon/clone/tests -q
```

The reusable harness is available as `clawbench-offline-clone`. Start with
[`docs/offline-clone-harness.md`](docs/offline-clone-harness.md) and
[`docs/offline-clone-amazon-case-study.md`](docs/offline-clone-amazon-case-study.md).
For the portable claw-bench-v2 expansion workflow, use
[`docs/claw-bench-v2-live-site-clone-plan.md`](docs/claw-bench-v2-live-site-clone-plan.md);
it reads the bundled inventory and does not require a sibling repository.

## Project governance

[`PROJECT.md`](PROJECT.md) defines the lifecycle, roles, definitions of done, and
expansion rules. `project/plan.json` is the machine-readable source of truth.

```bash
clawbench-project validate
clawbench-project status
clawbench-project check-release
```

The release check deliberately returns a non-zero status until every declared
gate has evidence and is marked `passed`.

## Harbor full-stack reconstruction benchmark

`clawbench-harbor` turns normalized site and instance authoring sources into
self-contained Harbor bundles. The Agent explores a browser-only reference with
Browser Use CLI, while the separate trusted verifier uses Playwright and direct
HTTP checks against reference and candidate services. Hidden checks, fixtures,
reference source, and oracle material are excluded from the Agent image.

Start with [`harbor/README.md`](harbor/README.md) and the complete
[`Harbor full-stack benchmark standard`](docs/harbor-fullstack-benchmark.md).
Generated tasks use Harbor's native Docker Compose reference sidecar and
separate verifier. Release still requires actual Harbor NOP/oracle runs and
human browser review; static schema validation is only the first gate.

## Amazon offline clone

The runnable clone lives in [`materials/amazon/clone/`](materials/amazon/clone/).
Its README documents startup, reset, SMTP, the public/admin boundary, frozen
commerce evidence, and known limits. The clone is intentionally an offline,
local simulation: payment, fulfillment, gift cards, seller flows, and email
delivery never claim to be connected to Amazon production systems.

Source evidence and its redistribution caveats are described in
[`materials/amazon/README.md`](materials/amazon/README.md). The capture was
anonymous and GET-only; reports omit cookies, authorization headers, and tokens.

### Permanent public demo

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https%3A%2F%2Fgithub.com%2Ftuxyw123%2FClawBench-WebsiteBench)

The Blueprint deploys the full clone from this repository's `main` branch as
one paid, non-sleeping container with a 1 GB persistent SQLite disk. The account
owner must review and approve the paid service and provide the access password
as a deploy-time secret; it is never committed. Read the
[`permanent demo guide`](docs/amazon-permanent-demo.md) before deployment:
this shared public demo accepts fictional data only and must remain
network-isolated from scored Agents and candidates. It does not replace the
per-run Harbor reference or any release gate.

## Viewer

Public static Viewer:

- [GitHub Pages](https://tuxyw123.github.io/ClawBench-WebsiteBench/)
- [Amazon worked example](https://tuxyw123.github.io/ClawBench-WebsiteBench/amazon/)

The Pages workflow publishes a project-path-safe copy of
`deploy/websitebench-cloudflare-worker/public`. It is a public inspection
surface, not evidence that the benchmark release gates have passed. Viewer
source changes must be regenerated and sanitized into that public snapshot
before they are published.

```bash
clawbench-viewer hash-password
export CLAWBENCH_VIEWER_USERNAME=reviewer
export CLAWBENCH_VIEWER_PASSWORD_HASH='$argon2id$...'
export CLAWBENCH_VIEWER_SESSION_SECRET='replace-with-at-least-32-random-characters'
export CLAWBENCH_VIEWER_COOKIE_SECURE=false
clawbench-viewer --repo-root . serve --profile internal
```

The Viewer keeps artifact readiness, human QA, official candidate scores, and
diagnostic image metrics separate. See
[`docs/websitebench-viewer.md`](docs/websitebench-viewer.md).

## Data and security boundary

- Secrets, `.env` files, run output, databases, reviews, and browser profiles
  are ignored by Git.
- Runtime clone assets are local; remote image dependencies are prohibited.
- Source-site media and markup remain the property of their respective owners.
  Review redistribution rights before making source evidence public.

See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
