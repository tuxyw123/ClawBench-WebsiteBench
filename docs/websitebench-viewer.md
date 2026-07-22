# WebsiteBench Clone Atlas

Clone Atlas is the WebsiteBench corpus and evaluation viewer. It is designed for
a growing benchmark with many offline websites, multiple categories, and results
from multiple model/harness combinations. It keeps four forms of evidence
separate:

- automatic artifact readiness (`present`, `missing`, `invalid`, or
  `not_applicable`);
- six-dimension human task QA and its gate;
- schema-valid `websitebench.result.v1` candidate scores;
- legacy verifier check counts.

It never derives a composite task-quality score. Viewer-side screenshot metrics
are explicitly diagnostic and are not official visual scores.

The overview presents the benchmark workflow, category coverage, and the
website-by-model evaluation matrix. The Websites catalog separates items that
are still being built from evaluation-ready and evaluated items. Models groups
runs by stable candidate identity, while Results is the cross-model result
table. A run page is the primary source-versus-clone comparison surface.

## Local use

Create an Argon2 hash and set credentials without committing them:

```bash
clawbench-viewer hash-password
export CLAWBENCH_VIEWER_USERNAME=reviewer
export CLAWBENCH_VIEWER_PASSWORD_HASH='$argon2id$...'
export CLAWBENCH_VIEWER_SESSION_SECRET='at-least-32-random-characters-change-me'
export CLAWBENCH_VIEWER_COOKIE_SECURE=false
clawbench-viewer --repo-root . serve --profile internal
```

The remaining commands are:

```bash
clawbench-viewer --repo-root . validate --profile internal
clawbench-viewer --repo-root . index --profile public --out public-index.json
clawbench-viewer --repo-root . publish --out public-viewer --base-path /
clawbench-viewer --repo-root . capture --item legacy--dev-115-freshdesk-invoice-dispute-ticket
clawbench-viewer --repo-root . capture --item legacy--dev-115-freshdesk-invoice-dispute-ticket \
  --run-id run-2026-07-22
clawbench-viewer --repo-root . capture --item legacy--dev-115-freshdesk-invoice-dispute-ticket \
  --checkpoint home-desktop --viewport desktop \
  --source-image source.png --candidate-image candidate.png
clawbench-viewer --repo-root . export-reviews --out reviews.json
```

Calling `capture` without images provisions companion records from every
declared checkpoint; it does not assume a fixed number of scenes. For a legacy
item it imports every explicit verifier screenshot as candidate-only evidence.
Providing `--run-id` keeps visual evidence isolated per model run.

Result reports may include an optional `candidate` object with `model_id`,
`display_name`, `provider`, `harness`, and `reasoning_effort`. The viewer uses
that metadata to build stable model groups and the evaluation matrix. Older
reports without it remain valid and appear under an unspecified candidate.

The public profile is built from
`websitebench/viewer-public-allowlist.json`. Its recursive leak check rejects
private fixture markers, internal commands, internal path fields, and absolute
workspace paths. Review writes/imports and the clone gateway are disabled in
that profile.

## Static publishing and deployment

`publish` renders every public overview, catalog, model, website, and run route
to path-safe static HTML, copies the public index and visual assets, and emits a
site manifest. The generated directory can be served by any static host.

## Cloudflare deployment

[`compose.yaml`](../deploy/websitebench-viewer/compose.yaml) runs the viewer and
Cloudflare Tunnel. Create five external Docker secrets named
`viewer_username`, `viewer_password_hash`, `viewer_session_secret`,
`viewer_trusted_hosts`, and `cloudflare_tunnel_token`. The trusted-host secret
contains the assigned hostname plus `localhost`. Cloudflare terminates TLS and
the application keeps the session cookie `Secure`, `HttpOnly`, and
`SameSite=Strict`.

The Dockerfile-specific ignore list excludes the repository `.env`, configured
model credentials, local artifacts, and test outputs from the image build
context.

The first deployment intentionally uses the authenticated `internal` profile.
No hostname, password, session secret, or tunnel token belongs in the repo.
