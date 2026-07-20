# ClawBench WebsiteBench

This repository is the standalone WebsiteBench subset extracted from
ClawBench. It contains everything needed to author, run, inspect, and evaluate
the current Web2Code2Web benchmark without carrying the unrelated live-web V1
and V2 corpora.

The production benchmark is **Northstar Market**, a synthetic white-label
commerce site. The Amazon material is retained as historical research evidence
showing how the initial source-observation and offline-clone workflow evolved;
it is not the production reference and is never exposed to candidate-building
agents.

## Repository map

| Path | Purpose |
| --- | --- |
| `websitebench/` | Public contracts, private reference, hidden judge, seeds, services, schemas, and Compose topology |
| `src/clawbench/web2code/` | Host CLI, isolation policy, candidate runtime, scoring, and reporting |
| `src/clawbench/viewer/` | Public Amazon benchmark site and authenticated review workspace |
| `src/clawbench/trajectory/` | Sanitized human/Agent offline trajectory export and validation |
| `tests/web2code/` | Contract, commerce, isolation, scoring, and reporting regression tests |
| `tests/viewer/` | Viewer discovery, security, reviews, visual evidence, and browser tests |
| `materials/amazon/` | Anonymous source capture, independently authored local clone, and selected gate evidence |
| `tasks/` | Amazon pilot task plus three small ClawBench legacy Viewer fixtures |
| `website-clone/` | Three legacy compatibility clones used by Viewer regression tests |
| `deploy/` | Viewer container and Cloudflare Tunnel deployment |
| `WebsiteBench.md` | Original benchmark requirements used to drive the implementation |

## Install and validate

Python 3.11 or newer is required. Docker Engine and Compose v2 are additionally
required for an isolated Agent-to-Candidate pilot.

```bash
python -m pip install -e '.[dev]'
python -m playwright install chromium

clawbench-web2code validate --site northstar-market
python -m pytest tests/web2code tests/viewer -q
```

Prepare a safe dry run without starting containers:

```bash
clawbench-web2code pilot \
  --site northstar-market \
  --track core \
  --model gpt-5.6-sol \
  --thinking-level xhigh \
  --dry-run
```

For a complete isolated run, set `OPENAI_API_KEY`, remove `--dry-run`, and use
a host with Docker and Compose. The pilot launches the private reference,
controlled BrowserUse gateway, Codex Agent, rootless builder, final no-egress
Candidate, mailbox, and hidden evaluator. See
[`websitebench/northstar-market/README.md`](websitebench/northstar-market/README.md).

## Viewer

```bash
clawbench-viewer hash-password
export CLAWBENCH_VIEWER_USERNAME=reviewer
export CLAWBENCH_VIEWER_PASSWORD_HASH='$argon2id$...'
export CLAWBENCH_VIEWER_SESSION_SECRET='replace-with-at-least-32-random-characters'
export CLAWBENCH_VIEWER_COOKIE_SECURE=false
clawbench-viewer --repo-root . serve --profile internal
```

The Viewer presents only the Amazon benchmark: public validation outcomes,
the 295-image Gate evidence registry, and explicitly published model runs.
The `internal` profile adds an authenticated Amazon review editor, raw reports,
and an allowlisted interactive clone; the `public` profile registers none of
those backend routes. The corpus and compatibility-clone files remain in the
repository but are not discovered by the Viewer. Publication metadata,
bilingual UI behavior, and deployment guidance are documented in
[`docs/websitebench-viewer.md`](docs/websitebench-viewer.md).

## Offline trajectory export

Export the retained Amazon clone-building history as explicitly retrospective
human/Agent provenance:

```bash
clawbench-trajectory export-clone materials/amazon/clone \
  --task tasks/clawbench/dev-136-amazon-t7-best-seller/task.json \
  --repo-root . \
  --out artifacts/offline-trajectories/amazon-dev-136 \
  --archive
```

The same CLI exports future live Web2Code run streams with `export-run` and
validates schemas, hashes, artifact references, and credential hygiene with
`validate`. See [`docs/offline-trajectory.md`](docs/offline-trajectory.md).

## Fused Amazon calibration runtime

The Amazon-shaped presentation, task-900136 request engine, and local account,
checkout, and order subsystem now run as one SQLite-backed site. Its canonical
paths and addresses live in
[`materials/amazon/runtime-manifest.json`](materials/amazon/runtime-manifest.json).
Run it from the repository root:

```bash
python materials/amazon/clone/server.py --host 127.0.0.1 --port 8153
```

Then open `http://127.0.0.1:8153/`. The corresponding ClawBench task is
[`tasks/clawbench/dev-136-amazon-t7-best-seller/task.json`](tasks/clawbench/dev-136-amazon-t7-best-seller/task.json).
The scored task still ends after adding two target drives; registration,
sign-in, checkout, and orders are additional interactive benchmark content.
See [`materials/amazon/README.md`](materials/amazon/README.md) before using or
redistributing source evidence.

## Data and security boundary

- Secrets, `.env` files, run output, databases, reviews, and browser profiles
  are ignored by Git.
- Northstar hidden fixtures and reference source are benchmark-maintainer data;
  the Compose topology prevents the Agent from reading them.
- The Amazon capture was anonymous and GET-only. Capture reports state that
  cookies, authorization headers, and tokens were omitted.
- Source-site media and markup remain the property of their respective owners.
  Keep this repository private unless the source-evidence redistribution scope
  has been reviewed separately.

See [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE) for repository licensing.
