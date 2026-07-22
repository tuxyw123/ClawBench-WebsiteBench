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
| `src/clawbench/viewer/` | Authenticated corpus-QA and result viewer |
| `tests/viewer/` | Viewer discovery, security, reviews, evidence, and browser tests |
| `tasks/` | Amazon task plus compact legacy Viewer fixtures |
| `website-clone/` | Legacy compatibility clones used by Viewer regression tests |
| `websitebench/schemas/` | Shared offline-clone, Viewer, and result schemas |
| `docs/` | Clone methodology, Amazon case study, and Viewer operation |

## Install and validate

Python 3.11 or newer is required.

```bash
python -m pip install -e '.[dev]'
python -m playwright install chromium
ruff check src tests websitebench
python -m pytest tests/offline_clone tests/viewer -q
python -m pytest materials/amazon/clone/tests -q
```

The reusable harness is available as `clawbench-offline-clone`. Start with
[`docs/offline-clone-harness.md`](docs/offline-clone-harness.md) and
[`docs/offline-clone-amazon-case-study.md`](docs/offline-clone-amazon-case-study.md).

## Amazon offline clone

The runnable clone lives in [`materials/amazon/clone/`](materials/amazon/clone/).
Its README documents startup, reset, SMTP, the public/admin boundary, frozen
commerce evidence, and known limits. The clone is intentionally an offline,
local simulation: payment, fulfillment, gift cards, seller flows, and email
delivery never claim to be connected to Amazon production systems.

Source evidence and its redistribution caveats are described in
[`materials/amazon/README.md`](materials/amazon/README.md). The capture was
anonymous and GET-only; reports omit cookies, authorization headers, and tokens.

## Viewer

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
