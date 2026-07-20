# WebsiteBench Amazon Viewer

The Viewer is a two-layer site for the retained Amazon reconstruction benchmark.
It does not scan or display the production corpus or compatibility clones.

## Public routes

Both deployment profiles expose these routes without authentication:

- `/` — benchmark summary, retained validation outcomes, compact leaderboard, and selected evidence;
- `/benchmark/amazon` — task contract, Gates 2–4, metrics, scope, and limits;
- `/leaderboard` — explicitly published model configurations;
- `/evidence` — all 295 retained Gate images, filtered and paginated 24 per page;
- `/methodology` — scoring, visual interpretation, safety boundaries, and evidence labels;
- `/runs/{run_id}` — aggregate detail for a published run only.

The language control switches all Viewer interface copy between English and
Chinese. English is the default and the choice persists in browser
`localStorage`. Raw technical reports remain in English.

The homepage illustration at
`src/clawbench/viewer/static/amazon-benchmark-hero.webp` was generated with the
built-in image-generation tool from a brand-free prompt. It contains a generic
browser, abstract commerce modules, and a small construction robot; no source
image, trademark, readable text, or commercial logo was used.

Source-site screenshots and derived comparisons may contain third-party public
content. The Viewer displays an ownership and redistribution notice; publishing
the site does not change the underlying rights or site terms.

## Profiles and local use

The `public` profile exposes only the anonymous layer. It does not require admin
credentials and does not register login, review, report, or clone routes:

```bash
clawbench-viewer --repo-root . serve --profile public
```

The `internal` profile exposes the same anonymous site plus `/login` and the
authenticated `/admin` review workspace:

```bash
clawbench-viewer hash-password
export CLAWBENCH_VIEWER_USERNAME=reviewer
export CLAWBENCH_VIEWER_PASSWORD_HASH='$argon2id$...'
export CLAWBENCH_VIEWER_SESSION_SECRET='at-least-32-random-characters-change-me'
export CLAWBENCH_VIEWER_COOKIE_SECURE=false
export CLAWBENCH_VIEWER_CLONE_ALLOWLIST=benchmark--amazon
clawbench-viewer --repo-root . serve --profile internal
```

After login, `/admin` provides the Amazon human-review editor, fixed raw-report
links, published/unpublished/invalid run inspection, review export/import APIs,
representative evidence, and the interactive clone. Review writes and imports
require both a session and CSRF token. Existing review files for other item keys
remain on disk but are neither loaded nor exported; imports containing another
key are rejected.

The clone gateway accepts only `benchmark--amazon` and only when that exact key
is present in `CLAWBENCH_VIEWER_CLONE_ALLOWLIST`. It starts the clone from its
canonical `materials/amazon/runtime-manifest.json` on port 8153 (or an unused
loopback port if occupied)
with a temporary SQLite database. The gateway is unavailable in the `public`
profile.

The manifest is the sole source for the Amazon task path, clone root,
entrypoint, commands, local/container URLs, and `/clone/benchmark--amazon/`
gateway path. Its attestation set includes the Amazon server, FastAPI edge,
SQLite commerce Adapter, shared account/order Interface, templates, assets,
source fixtures, and verifier tools. The Viewer marks retained Gates `stale`
when any current runtime fingerprint is missing from or differs from a report.
The existing 2026-07-18 Gate 4 approval is therefore shown as historical after
the commerce fusion, not as current approval.

Inside the clone, registration, local verification, login/logout, password
reset, guest-cart migration, local test checkout, order history, account
isolation, and cancellation share the same SQLite state as the scored Amazon
cart. No real email, payment, delivery, or external order call is made; raw
passwords, account tokens, authenticated-session tokens, and card numbers are
not persisted.

Useful read-only commands are:

```bash
clawbench-viewer --repo-root . validate --profile internal
clawbench-viewer --repo-root . validate --profile public
clawbench-viewer --repo-root . index --profile public --out public-index.json
clawbench-viewer --repo-root . export-reviews --out amazon-review.json
```

## Publishing model runs

Discovery supports the current evaluator output and the compatibility path:

```text
artifacts/websitebench/runs/<run>/eval/evaluation-result.json
artifacts/websitebench/runs/<run>/report.json
```

Only a schema-valid `websitebench.result.v1` report with `site_id=amazon` is
accepted. Model publication metadata comes from `run-meta.json` in the run
directory. A run appears publicly only when it includes all three fields:

```json
{
  "model": "model-name",
  "thinking_level": "high",
  "viewer_public": true
}
```

The leaderboard groups by `(model, thinking_level, track)`, keeps the highest
score, and resolves equal scores in favor of the latest completion. It then
sorts by total score descending, visual score descending, and model name
ascending. With no explicitly published run, the truthful UI state is “No
published runs yet.”

### Unranked harness calibration

The benchmark page separately discovers
`artifacts/websitebench/calibrations/**/calibration-result.json`. Each file must
validate as `websitebench.calibration-result.v1` for Amazon-136. The section
shows reasoning effort, PASS/FAIL/HARNESS_ERROR, step rate, tokens, browser
actions, elapsed time, and harness error. Calibration records have no official
score fields, never display `/100`, and are excluded from result discovery and
leaderboard aggregation.

Public run detail contains aggregate scores, resources, network facts, and
usage. Hidden journeys, seeds, failure reproduction, versions, internal paths,
and evidence manifests remain internal.

## Evidence routing

The adapter registers images from `materials/amazon/verification/gate2`,
`gate3`, and `gate4`. Requests use generated evidence IDs, never filesystem
paths. Arbitrary paths, traversal, unregistered files, and any path that crosses
a symlink are rejected. The source files are read-only.

## Container deployment

[`compose.yaml`](../deploy/websitebench-viewer/compose.yaml) runs the internal
profile with Cloudflare Tunnel. Create external Docker secrets named
`viewer_username`, `viewer_password_hash`, `viewer_session_secret`,
`viewer_trusted_hosts`, and `cloudflare_tunnel_token`. The trusted-host value
contains the assigned hostname plus `localhost`. Cloudflare terminates TLS;
session cookies stay `Secure`, `HttpOnly`, and `SameSite=Strict`.

The compose file mounts a read-only run directory. Set
`CLAWBENCH_VIEWER_RUNS_DIR` when results live outside the repository artifact
path. The image includes the fixed Amazon task, reports, and evidence; secrets,
local review data, databases, and run artifacts are not copied into the image.

For a public-only container, run the same image with `serve --profile public`
and omit all admin secrets, review volumes, and the clone allowlist.
